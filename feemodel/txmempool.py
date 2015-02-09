from __future__ import division

import threading
import sqlite3
import os
import decimal
import logging
from time import time
from copy import deepcopy

from bitcoin.core import b2lx

from feemodel.config import history_file, poll_period, keep_history
from feemodel.util import proxy, StoppableThread, get_feerate

logger = logging.getLogger(__name__)

MEMBLOCK_TABLE_SCHEMA = {
    'blocks': [
        'height INTEGER UNIQUE',
        'size INTEGER',
        'time INTEGER'
    ],
    'txs': [
        'blockheight INTEGER',
        'txid TEXT',
        'size INTEGER',
        'fee TEXT',
        'startingpriority TEXT',
        'currentpriority TEXT',
        'time INTEGER',
        'height INTEGER',
        'depends TEXT',
        'feerate INTEGER',
        'leadtime INTEGER',
        'isconflict INTEGER',
        'inblock INTEGER'
    ]
}


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

    The polling is done via batch call; however, they are not processed
    atomically by Bitcoin Core - there is the probability of a race condition
    in which the block count increases in between processing the two requests.
    In this case the statistics for that block will be somewhat degraded.

    In addition, chain re-orgs are not handled. If a re-org happens, the
    transactions that we record are not necessarily representative of the
    pool of valid transactions seen by the miner. Any inference algorithm
    must be tolerant of such errors, in addition to any other kinds of network
    errors.
    '''
    # TODO: handle RPC errors. Also qualify all excepts.
    def __init__(self, write_history=True, dbfile=history_file,
                 keep_history=keep_history):
        self.history_lock = threading.Lock()
        self.mempool_lock = threading.Lock()
        self.best_height = None
        self.rawmempool = None
        self.write_history = write_history
        self.dbfile = dbfile
        self.keep_history = keep_history
        super(self.__class__, self).__init__()

    def run(self):
        '''Target function of the thread.
        Polls mempool every poll_period seconds until stop flag is set.
        '''
        logger.info("Starting TxMempool")
        self.best_height, self.rawmempool = proxy.poll_mempool()
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
        curr_height, rawmp_new = proxy.poll_mempool()
        with self.mempool_lock:
            if curr_height > self.best_height:
                entries = {txid: MemEntry(rawentry)
                           for txid, rawentry in self.rawmempool.iteritems()}
                threading.Thread(
                    target=self.process_blocks,
                    args=(range(self.best_height+1, curr_height+1),
                          entries, set(rawmp_new)),
                    name='mempool-processblocks').start()
                self.rawmempool = rawmp_new
                self.best_height = curr_height
                return True
            else:
                self.rawmempool = rawmp_new
                return False

    def process_blocks(self, blockheight_range, entries, new_entries_ids):
        '''Called when block count has increased.

        Records the mempool entries in a MemBlock instance and writes to disk.
        entries is a dict that maps txids to MemEntry objects.
        '''
        with self.history_lock:
            memblocks = []
            for height in blockheight_range:
                block = proxy.getblock(proxy.getblockhash(height))
                memblocks.append(MemBlock(height, block, entries))

            # The set of transactions that were removed from the mempool, yet
            # were not included in a block.
            conflicts = set(entries) - new_entries_ids
            for txid in conflicts:
                # For the first block, label the MemBlock entries that are
                # conflicts. Assume the conflict was removed after the first
                # block, so remove them from the remaining blocks.
                memblocks[0].entries[txid].isconflict = True
                for memblock in memblocks[1:]:
                    del memblock.entries[txid]
            if len(conflicts):
                logger.info("process_blocks: %d conflicts removed." %
                            len(conflicts))

            if self.write_history and self.is_alive():
                for memblock in memblocks:
                    try:
                        memblock.write(self.dbfile, self.keep_history)
                    except Exception:
                        logger.exception("MemBlock write/del exception.")

            return memblocks

    def get_entries(self):
        '''Returns mempool entries.'''
        if self.rawmempool is None:
            raise ValueError("No mempool data.")
        with self.mempool_lock:
            entries = {txid: MemEntry(rawentry)
                       for txid, rawentry in self.rawmempool.iteritems()}
            return entries


class MemBlock(object):
    '''Represents the mempool state at the time a block was discovered.

    Instance vars:
        entries - {txid: entry} dict of mempool entries just prior to block
                  discovery time. entry is a MemEntry object.
        height - The block height
        size - The block size in bytes
        time - The block discovery time as recorded by this node
               (not the block timestamp).

    Methods:
        write - Write to disk.
        read - Read from disk.
        get_block_list - Get the list of heights of stored blocks.
    '''

    def __init__(self, blockheight=None, block=None, entries=None):
        '''Label the mempool entries based on the block data.

        We record various block statistics, and for each MemEntry we label
        inblock and leadtime. See MemEntry for more info.
        '''
        # TODO: add warning if measured time differs greatly from timestamp
        if blockheight and block and entries is not None:
            self.height = blockheight
            self.size = len(block.serialize())
            self.time = int(time())
            self.entries = deepcopy(entries)
            for entry in self.entries.values():
                entry.inblock = False
                entry.isconflict = False
                entry.leadtime = self.time - entry.time

            blocktxs = [b2lx(tx.GetHash()) for tx in block.vtx]
            entries_inblock = set(entries) & set(blocktxs)
            for txid in entries_inblock:
                self.entries[txid].inblock = True
                del entries[txid]

            # As a measure of our node's connectivity, we want to note the
            # size of the intersection of set(blocktxs) and
            # set(entries_prev). If it is low, it means that our node is
            # not being informed of many transactions.
            incl_text = 'MemBlock: %d of %d in block %d' % (
                len(entries_inblock), len(blocktxs)-1, blockheight)
            logger.info(incl_text)
            if len(blocktxs) > 1:
                incl_ratio = len(entries_inblock) / (len(blocktxs)-1)
                if incl_ratio < 0.9:
                    logger.warning(incl_text)

    def write(self, dbfile, keep_history):
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
                    for key, val in MEMBLOCK_TABLE_SCHEMA.items():
                        db.execute('CREATE TABLE %s (%s)' %
                                   (key, ','.join(val)))

            db.execute('CREATE INDEX IF NOT EXISTS heightidx '
                       'ON txs (blockheight)')
            with db:
                db.execute('INSERT INTO blocks VALUES (?,?,?)',
                           (self.height, self.size, self.time))
                db.executemany(
                    'INSERT INTO txs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    [(self.height, txid) + entry._get_attr_tuple()
                     for txid, entry in self.entries.iteritems()])
            if keep_history > 0:
                history_limit = self.height - keep_history
                with db:
                    db.execute('DELETE FROM blocks WHERE height<=?',
                               (history_limit,))
                    db.execute('DELETE FROM txs WHERE blockheight<=?',
                               (history_limit,))
        finally:
            if db:
                db.close()

    @classmethod
    def read(cls, blockheight, dbfile=history_file):
        '''Read MemBlock from disk.
        Returns the memblock with specified blockheight.
        Returns None if no record exists for that block.
        Raises one of the sqlite3 errors if there are other problems.
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
            txlist = db.execute('SELECT * FROM txs WHERE blockheight=?',
                                (blockheight,))
            memblock = cls()
            memblock.height = blockheight
            memblock.size = blocksize
            memblock.time = blocktime
            memblock.entries = {
                tx[1]: MemEntry._from_attr_tuple(tx[2:]) for tx in txlist}
            return memblock
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
        return "MemBlock{height: %d, size: %d, num entries: %d}" % (
            self.height, self.size, len(self.entries))

    def __eq__(self, other):
        if not isinstance(other, MemBlock):
            return False
        return self.__dict__ == other.__dict__


class MemEntry(object):
    '''Represents a mempool entry.

    This is basically the data returned by getrawmempool, but with additional
    attributes if it is associated with a MemBlock:
        inblock - whether or not the transaction was included in the block
        leadtime - difference between block discovery time and mempool entry
                   time
        isconflict - whether or not the transaction is a conflict, meaning
                     that it was subsequently removed from the mempool as a
                     result of being invalidated by some other transaction
                     in the block.
    In addition, for convenience we compute and store the feerate (satoshis
    per kB of transaction size)
    '''
    def __init__(self, rawmempool_entry=None):
        if rawmempool_entry:
            self.size = rawmempool_entry['size']
            self.fee = rawmempool_entry['fee']
            self.startingpriority = rawmempool_entry['startingpriority']
            self.currentpriority = rawmempool_entry['currentpriority']
            self.time = rawmempool_entry['time']
            self.height = rawmempool_entry['height']
            self.depends = rawmempool_entry['depends'][:]

            # Additional fields
            self.feerate = get_feerate(rawmempool_entry)
            self.leadtime = None
            self.isconflict = None
            self.inblock = None

    def _get_attr_tuple(self):
        for attr in ['leadtime', 'isconflict', 'inblock']:
            if getattr(self, attr) is None:
                raise ValueError("MemEntry not yet processed with MemBlock.")
        attr_tuple = (
            self.size,
            str(self.fee),
            str(self.startingpriority),
            str(self.currentpriority),
            self.time,
            self.height,
            ','.join(self.depends),
            self.feerate,
            self.leadtime,
            self.isconflict,
            self.inblock
        )
        return attr_tuple

    @classmethod
    def _from_attr_tuple(cls, tup):
        m = cls()

        (m.size, m.fee, m.startingpriority, m.currentpriority,
         m.time, m.height, m.depends, m.feerate, m.leadtime,
         m.isconflict, m.inblock) = tup

        m.fee = decimal.Decimal(m.fee)
        m.currentpriority = decimal.Decimal(m.currentpriority)
        m.startingpriority = decimal.Decimal(m.startingpriority)
        m.depends = m.depends.split(',') if m.depends else []
        m.isconflict = bool(m.isconflict)
        m.inblock = bool(m.inblock)

        return m

    def __repr__(self):
        return str(self.__dict__)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


def check_missed_blocks(start, end):
    '''Check for MemBlocks missing from the db in range(start, end).'''
    missed_blocks = [height for height in range(start, end)
                     if not MemBlock.read(height)]
    print("%d missed blocks out of %d." %
          (len(missed_blocks), end-start))

    return missed_blocks


def get_mempool_size(minfeerate):
    '''Get size of mempool.

    Returns size of mempool in bytes for all transactions that have a feerate
    >= minfeerate.
    '''
    rawmempool = proxy.getrawmempool(verbose=True)
    txs = [MemEntry(entry) for entry in rawmempool.values()]
    return sum([tx.size for tx in txs if tx.feerate >= minfeerate])


def get_mempool():
    rawmempool = proxy.getrawmempool(verbose=True)
    return {txid: MemEntry(entry) for txid, entry in rawmempool.items()}


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
