import threading
import sqlite3
import json
import os
import decimal
import logging
from time import time
from copy import deepcopy

from bitcoin.core import COIN, b2lx

from feemodel.config import config, history_file
from feemodel.util import proxy, StoppableThread

history_lock = threading.Lock()
mempool_lock = threading.Lock()
logger = logging.getLogger(__name__)

poll_period = config.getint('txmempool', 'poll_period')
keep_history = config.getint('txmempool', 'keep_history')


class TxMempool(StoppableThread):
    '''Thread that tracks the mempool state at points of block discovery.

    When the thread is running, Bitcoin Core is polled every poll_period
    seconds over JSON-RPC for:
        1. The current block count, via getblockcount().
        2. The transactions in the mempool, via getrawmempool(verbose=True)

    If the block count has increased, we record:
        1. Transactions in the mempool just prior to block discovery
        2. For each transaction, whether or not it was included in the block.

    The goal is to make inferences about the transaction selection policies
    of miners.

    The mempool polling is done via batch call; however, they are not
    processed atomically by Bitcoin Core - there is the probability of a
    race condition in which the block count increases in between processing
    the two requests. In this case the statistics for that block will be
    somewhat degraded.

    In addition, chain re-orgs are not handled. If a re-org happens, the
    transactions that we record are not necessarily representative of the
    pool of valid transactions seen by the miner.
    '''
    # Have to handle RPC errors

    def run(self):
        '''Target function of the thread.
        Polls mempool every poll_period seconds until stop flag is set.
        '''
        logger.info("Starting TxMempool")
        self.best_height, self.entries = proxy.poll_mempool()
        while not self.is_stopped():
            self.update()
            self.sleep(poll_period)
        logger.info("Stopping TxMempool..")
        for thread in threading.enumerate():
            if thread.name.startswith('mempool'):
                thread.join()
        logger.info("TxMempool stopped.")

    def update(self):
        '''Mempool polling function.'''
        curr_height, entries_new = proxy.poll_mempool()
        with mempool_lock:
            if curr_height > self.best_height:
                threading.Thread(
                    target=self.process_blocks,
                    args=(range(self.best_height+1, curr_height+1),
                          deepcopy(self.entries),
                          deepcopy(entries_new)),
                    name='mempool-processblocks').start()
                self.entries = entries_new
                self.best_height = curr_height
                return True
            else:
                self.entries = entries_new
                return False

    def process_blocks(self, blockheight_range, entries_prev, entries_new,
                       blocktime=None):
        '''Called when block count has increased.

        Records the mempool entries in a MemBlock instance and writes to disk.

        entries is a dict that maps txids to entry objects.
        entry represents a transaction: the dict that is returned by
        getrawmempool(verbose=True), plus some additional keys:
            inblock - whether or not the transaction was included in the block
            leadtime - difference between block discovery and mempool entry
                       time of the transaction.
            feerate - fee per kB of transaction size
            isconflict - whether or not the transaction is a conflict, meaning
                         that it was removed from the mempool as a result of
                         being invalidated by some other transaction in the
                         block.
        '''
        with history_lock:
            if not blocktime:
                blocktime = int(time())
            memblocks = []
            for height in blockheight_range:
                block = proxy.getblock(proxy.getblockhash(height))
                blocksize = len(block.serialize())
                blocktxs = [b2lx(tx.GetHash()) for tx in block.vtx]
                entries = deepcopy(entries_prev)

                num_memtxs_inblock = 0
                for txid, entry in entries.iteritems():
                    if txid in blocktxs:
                        entry['inblock'] = True
                        num_memtxs_inblock += 1
                        del entries_prev[txid]
                    else:
                        entry['inblock'] = False
                    entry['leadtime'] = blocktime - entry['time']
                    entry['feerate'] = int(
                        entry['fee']*COIN) * 1000 // (entry['size'])
                    entry['isconflict'] = False

                memblocks.append(
                    MemBlock(entries, height, blocksize, blocktime))

                # As a measure of our node's connectivity, we want to note the
                # size of the intersection of set(blocktxs) and
                # set(entries_prev). If it is low, it means that our node is
                # not being informed of many transactions.
                incl_text = 'process_blocks: %d of %d in block %d' % (
                            num_memtxs_inblock, len(blocktxs)-1, height)
                logger.info(incl_text)
                incl_ratio = num_memtxs_inblock / float(len(blocktxs) - 1)
                if incl_ratio < 0.9:
                    logger.warning(incl_text)

            # The set of transactions that were removed from the mempool, yet
            # were not included in a block.
            conflicts = set(entries_prev) - set(entries_new)

            for txid in conflicts:
                # Assume the conflict was removed after the first block.
                memblocks[0].entries[txid]['isconflict'] = True
                for memblock in memblocks[1:]:
                    del memblock.entries[txid]

            if len(conflicts):
                logger.warning("process_blocks: %d conflicts removed." %
                               len(conflicts))

            if self and self.is_alive():
                for memblock in memblocks:
                    memblock.write()

            return memblocks

    def get_mempool(self):
        '''Returns a deepcopy of mempool entries.'''
        with mempool_lock:
            return deepcopy(self.entries)


class MemBlock(object):
    '''Info about the mempool state at the time a block was discovered.

    Instance vars:
        entries - {txid: entry} dict of mempool entries at block discovery
                  time. entry is the tx dict returned by
                  getrawmempool(verbose=True) with additional keys.*
        height - The block height
        size - The block size in bytes
        time - The block discovery time as recorded by this node
               (not the block timestamp).

        * Additional keys:
            inblock - whether or not the transaction was included in the block
            leadtime - difference between block discovery and mempool entry
                       time of the transaction.
            feerate - fee per kB of transaction size
            isconflict - whether or not the transaction is a conflict, meaning
                         that it was removed from the mempool as a result of
                         being invalidated by some other transaction in the
                         block.

    Methods:
        write - Write to disk.
        read - Read from disk.
        get_block_list - Get the list of stored block heights.
    '''

    def __init__(self, entries, blockheight, blocksize, blocktime):
        self.entries = entries
        self.height = blockheight
        self.size = blocksize
        self.time = blocktime

    def write(self, dbfile=history_file, keep_history=keep_history):
        '''Write MemBlock to disk.

        keep_history specifies how many blocks of information should be
        retained. All MemBlocks older (with respect to this block) than
        keep_history will be deleted.
        '''
        db = None
        db_exists = os.path.exists(dbfile)
        try:
            db = sqlite3.connect(dbfile)
            if not db_exists:
                with db:
                    db.execute(
                        'CREATE TABLE blocks '
                        '(height INTEGER UNIQUE, size INTEGER, time REAL)')
                    db.execute(
                        'CREATE TABLE txs '
                        '(blockheight INTEGER, txid TEXT, data TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS heightidx '
                       'ON txs (blockheight)')
            with db:
                db.execute('INSERT INTO blocks VALUES (?,?,?)',
                           (self.height, self.size, self.time))
                db.executemany(
                    'INSERT INTO txs VALUES (?,?,?)',
                    [(self.height, txid,
                      json.dumps(entry, default=decimal_default))
                     for txid, entry in self.entries.iteritems()])
            if keep_history > 0:
                history_limit = self.height - keep_history
                with db:
                    db.execute('DELETE FROM blocks WHERE height<=?',
                               (history_limit,))
                    db.execute('DELETE FROM txs WHERE blockheight<=?',
                               (history_limit,))
        except:
            logger.error("MemBlock: Exception in writing/deleting history.")
            logger.debug("sqlite error", exc_info=True)
        finally:
            if db:
                db.close()

    @classmethod
    def read(cls, blockheight, dbfile=history_file):
        '''Read MemBlock from disk.
        Returns the memblock with specified blockheight.
        '''
        db = None
        try:
            db = sqlite3.connect(dbfile)
            block = db.execute('SELECT size, time FROM blocks WHERE height=?',
                               (blockheight,)).fetchall()
            if block:
                blocksize, blocktime = block[0]
            else:
                return None
            txlist = db.execute('SELECT txid, data FROM txs '
                                'WHERE blockheight=?',
                                (blockheight,))
            entries = {txid: json.loads(data) for txid, data in txlist}
            for entry in entries.itervalues():
                entry['fee'] = decimal.Decimal(entry['fee'])
                entry['startingpriority'] = decimal.Decimal(
                    entry['startingpriority'])
                entry['currentpriority'] = decimal.Decimal(
                    entry['currentpriority'])
            return cls(entries, blockheight, blocksize, blocktime)
        except:
            logger.exception("MemBlock: Unable to read history.")
            return None
        finally:
            if db:
                db.close()

    @staticmethod
    def get_block_list(dbfile=history_file):
        '''Get the list of heights of all MemBlocks stored on disk.'''
        db = None
        try:
            db = sqlite3.connect(dbfile)
            memblock_heights = db.execute(
                'SELECT height FROM blocks').fetchall()
            return [b[0] for b in memblock_heights]
        finally:
            if db:
                db.close()

    def __repr__(self):
        return "MemBlock{height: %d, size: %d, num entries: %d" % (
            self.height, self.size, len(self.entries))

    def __eq__(self, other):
        if not isinstance(other, MemBlock):
            return False
        return self.__dict__ == other.__dict__


def check_missed_blocks(start, end):
    '''Check for MemBlocks missing from the db in range(start, end).'''
    missed_blocks = [height for height in range(start, end)
                     if not MemBlock.read(height)]
    print("%d missed blocks out of %d." %
          (len(missed_blocks), end-start))

    return missed_blocks


def decimal_default(obj):
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    raise TypeError


# class LoadHistory(object):
#     def __init__(self, dbfile=historyFile):
#         self.fns = []
#         self.dbfile = dbfile
#
#     def registerFn(self, fn, blockHeightRange):
#         # blockHeightRange tuple (start,end) includes start but not end,
#         # to adhere to range() convention
#         self.fns.append((fn, blockHeightRange))
#
#     def loadBlocks(self):
#         startHeight = min([f[1][0] for f in self.fns])
#         endHeight = max([f[1][1] for f in self.fns])
#
#         for height in range(startHeight, endHeight):
#             block = Block.blockFromHistory(height, self.dbfile)
#             for fn, blockHeightRange in self.fns:
#                 if height >= blockHeightRange[0] and (
#                         height < blockHeightRange[1]):
#                     fn([block])
#
