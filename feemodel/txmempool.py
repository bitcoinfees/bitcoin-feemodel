from __future__ import division

import threading
import sqlite3
import decimal
import logging
from Queue import Queue
from time import time
from copy import deepcopy

from bitcoin.core import b2lx

from feemodel.config import (history_file, poll_period, keep_history,
                             minrelaytxfee, prioritythresh)
from feemodel.util import proxy, StoppableThread, get_feerate
from feemodel.stranding import tx_preprocess, calc_stranding_feerate

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

# We're having db concurrency problems, so add our own lock for now
db_lock = threading.Lock()


class TxMempool(StoppableThread):
    '''Thread that tracks the mempool state at points of block discovery.

    When the thread is running, Bitcoin Core is polled every poll_period
    seconds over JSON-RPC for:
        1. The current block count, via getblockcount().
        2. The transactions in the mempool, via getrawmempool(verbose=True)

    If the block count has increased in between polls, we record:
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

    PROCESS_STOP = 'stop'  # Sentinel value for stopping process worker thread

    def __init__(self, write_history=True, dbfile=history_file,
                 keep_history=keep_history):
        self.mempool_lock = threading.Lock()
        self.blockqueue = Queue()
        self.best_height = None
        self.rawmempool = None
        self.write_history = write_history
        self.dbfile = dbfile
        self.keep_history = keep_history
        self.starttime = time()
        super(TxMempool, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        '''Target function of the thread.

        Polls mempool every poll_period seconds until stop flag is set.
        '''
        logger.info("Starting TxMempool")
        self.process_thread = threading.Thread(target=self.process_worker)
        self.process_thread.start()
        try:
            self.best_height, self.rawmempool = proxy.poll_mempool()
            while not self.is_stopped():
                self.update()
                self.sleep(poll_period)
        finally:
            self.blockqueue.put(self.PROCESS_STOP)
            self.process_thread.join()
            self.rawmempool = None
            logger.info("TxMempool stopped.")

    def update(self):
        '''Mempool polling function.'''
        curr_height, rawmp_new = proxy.poll_mempool()
        if curr_height > self.best_height:
            process_args = (
                range(self.best_height+1, curr_height+1),
                {
                    txid: MemEntry(rawentry)
                    for txid, rawentry in self.rawmempool.iteritems()},
                set(rawmp_new),
                int(time())
            )
            self.blockqueue.put(process_args)
        with self.mempool_lock:
            self.best_height = curr_height
            self.rawmempool = rawmp_new

    def process_worker(self):
        '''Target function for a worker thread that processes new memblocks.'''
        while True:
            args = self.blockqueue.get()
            if args == self.PROCESS_STOP:
                break
            self.process_blocks(*args)
        logger.info("Process worker received stop sentinel; thread complete.")

    def process_blocks(self, blockheight_range, entries,
                       new_entries_ids, blocktime):
        '''Called when block count has increased.

        Records the mempool entries in a MemBlock instance and writes to disk.
        entries is a dict that maps txids to MemEntry objects.
        new_entries_ids is the set of txids in the mempool immediately after
        the block(s).
        '''
        memblocks = []
        for height in blockheight_range:
            block = proxy.getblock(proxy.getblockhash(height))
            memblocks.append(MemBlock(height, blocktime, block, entries))

        # The set of transactions that were removed from the mempool, yet
        # were not included in a block.
        conflicts = set(entries) - new_entries_ids
        conflicts_size = 0
        for txid in conflicts:
            # For the first block, label the MemBlock entries that are
            # conflicts. Assume the conflict was removed after the first
            # block, so remove them from the remaining blocks.
            memblocks[0].entries[txid].isconflict = True
            conflicts_size += memblocks[0].entries[txid].size
            for memblock in memblocks[1:]:
                del memblock.entries[txid]
        if len(conflicts):
            logger.info("process_blocks: %d conflicts "
                        "(%d bytes) removed." %
                        (len(conflicts), conflicts_size))
        if conflicts_size > 10000:
            # If many conflicts are removed, it can screw up the txsource
            # estimation; so log a warning.
            logger.warning(
                "process_blocks: %d bytes of conflicts removed." %
                conflicts_size)

        for memblock in memblocks:
            txs = tx_preprocess(memblock)
            if txs:
                stats = calc_stranding_feerate(txs, bootstrap=False)
                logger.info("Block {}: stranding feerate is {}".
                            format(memblock.height, stats['sfr']))
            else:
                logger.info("Block {}: no txs.".format(memblock.height))
            if self.write_history and self.is_alive():
                try:
                    memblock.write(self.dbfile, self.keep_history)
                except Exception:
                    logger.exception("MemBlock write/del exception.")

        return memblocks

    def get_entries(self):
        '''Returns mempool entries.'''
        with self.mempool_lock:
            rawmempool = self.rawmempool
            best_height = self.best_height
        if rawmempool is None:
            raise ValueError("No mempool data")
        entries = {txid: MemEntry(rawentry)
                   for txid, rawentry in rawmempool.iteritems()}
        return entries, best_height

    def get_status(self):
        runtime = int(time() - self.starttime)
        currheight = proxy.getblockcount()
        numhistory = len(MemBlock.get_heights())
        if self.rawmempool:
            mempool_status = 'running'
        else:
            mempool_status = 'stopped'
        status = {
            'runtime': runtime,
            'height': currheight,
            'numhistory': numhistory,
            'mempool': mempool_status}
        return status

    def __nonzero__(self):
        return self.rawmempool is not None


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
        get_heights - Get the list of heights of stored blocks.
    '''

    def __init__(self, blockheight=None, blocktime=None,
                 block=None, entries=None):
        '''Label the mempool entries based on the block data.

        We record various block statistics, and for each MemEntry we label
        inblock and leadtime. See MemEntry for more info.
        '''
        # TODO: add warning if measured time differs greatly from timestamp
        if blockheight and blocktime and block and entries is not None:
            self.height = blockheight
            self.size = len(block.serialize())
            self.time = blocktime
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

            incl_text = 'Block {}: {}/{} in mempool'.format(
                blockheight, len(entries_inblock), len(blocktxs)-1)
            logger.info(incl_text)

            # As a measure of our node's connectivity, we want to note the
            # ratio below. If it is low, it means that our node is not being
            # informed of many transactions.
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
        try:
            db = sqlite3.connect(dbfile)
            for key, val in MEMBLOCK_TABLE_SCHEMA.items():
                db.execute('CREATE TABLE IF NOT EXISTS %s (%s)' %
                           (key, ','.join(val)))
            db.execute('CREATE INDEX IF NOT EXISTS heightidx '
                       'ON txs (blockheight)')
            with db_lock:
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
            if db is not None:
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
            with db_lock:
                block = db.execute('SELECT size, time FROM blocks '
                                   'WHERE height=?',
                                   (blockheight,)).fetchall()
                txlist = db.execute('SELECT * FROM txs WHERE blockheight=?',
                                    (blockheight,)).fetchall()
        except sqlite3.OperationalError as e:
            if e.message.startswith('no such table'):
                return None
            raise e
        else:
            if block:
                blocksize, blocktime = block[0]
            else:
                return None
            memblock = cls()
            memblock.height = blockheight
            memblock.size = blocksize
            memblock.time = blocktime
            memblock.entries = {
                tx[1]: MemEntry._from_attr_tuple(tx[2:]) for tx in txlist}
            return memblock
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def get_heights(blockrangetuple=None, dbfile=history_file):
        '''Get the list of MemBlocks stored on disk.

        Returns a list of heights of all MemBlocks on disk within
        range(*blockrangetuple)
        '''
        if blockrangetuple is None:
            blockrangetuple = (0, float("inf"))
        db = None
        try:
            db = sqlite3.connect(dbfile)
            with db_lock:
                heights = db.execute(
                    'SELECT height FROM blocks '
                    'where height>=? and height <?',
                    blockrangetuple).fetchall()
            return [r[0] for r in heights]
        except sqlite3.OperationalError as e:
            if e.message.startswith('no such table'):
                return []
            raise e
        finally:
            if db is not None:
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
    per kB of transaction size).

    Also, care is taken not to mutate the rawmempool_entry input.
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

    def is_high_priority(self):
        '''Check if entry is high priority.

        Returns True if entry is considered high priority by Bitcoin Core
        with regard to priority inclusion in the next block.

        Ideally this should simply return
        (entry.currentpriority > prioritythresh), however, currentpriority,
        as obtained by RPC, uses the current height, whereas miners in forming
        a new block use the current height + 1, i.e. the height of the new
        block. So currentpriority underestimates the 'true' mining priority.
        (There are other complications, in that currentpriority doesn't take
        into account cases where the entry has mempool dependencies, but
        that's a different problem, which we live with for now.)

        This difference is important because, for the purposes of minfeerate
        policy estimation, we need to properly exclude all high priority
        transactions. Previously in v0.9 of Bitcoin Core, this wasn't such a
        big problem, because low priority transactions below minrelaytxfee
        are still relayed / enter the mempool; there are thus sufficient
        low-fee, low-priority transactions so that the minfeerate threshold
        is still estimatable in a consistent manner.

        With v0.10, however, only high (miners') priority transactions are
        allowed into the mempool if the tx has low fee. If one relies on the
        criteria (entry.currentpriority > prioritythresh), there will be false
        negatives; however because there aren't any more truly low-priority
        transactions with similar feerate, the minfeerate estimation can
        become inconsistent.

        It's not possible, however, to adjust entry.currentpriority to become
        the miners' priority, solely from the information obtained from
        getrawmempool. Therefore, we resort to this hack: the entry is classed
        as high priority if (entry.currentpriority > prioritythresh) or
        (entry.feerate < minrelaytxfee).
        '''
        return (self.currentpriority > prioritythresh or
                self.feerate < minrelaytxfee)

    def _get_attr_tuple(self):
        '''Get tuple of attributes.
        Used when writing MemBlock to disk.
        '''
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
        '''Form MemEntry from attribute tuple.
        Used when reading MemBlock from disk.
        '''
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
