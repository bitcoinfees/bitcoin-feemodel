from __future__ import division

import os
import threading
import sqlite3
import decimal
import logging
from Queue import Queue
from time import time
from copy import deepcopy

from bitcoin.core import b2lx

from feemodel.config import (memblock_dbfile, poll_period,
                             minrelaytxfee, prioritythresh, blocks_to_keep)
from feemodel.util import proxy, StoppableThread, get_feerate
from feemodel.stranding import tx_preprocess, calc_stranding_feerate
from feemodel.simul.simul import SimEntry

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

    WORKER_STOP = 'stop'  # Sentinel value for stopping block worker thread

    def __init__(self, dbfile=memblock_dbfile, blocks_to_keep=blocks_to_keep):
        self.state = None
        self.blockqueue = Queue()
        self.dbfile = dbfile
        self.blocks_to_keep = blocks_to_keep
        self.starttime = time()
        super(TxMempool, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        '''Target function of the thread.

        Polls mempool every poll_period seconds until stop flag is set.
        '''
        logger.info("Starting TxMempool")
        self.blockworker = threading.Thread(target=self.blockworker_target)
        self.blockworker.start()
        try:
            self.state = MempoolState(*proxy.poll_mempool())
            while not self.is_stopped():
                self.update()
                self.sleep(poll_period)
        finally:
            self.blockqueue.put(self.WORKER_STOP)
            self.blockworker.join()
            self.state = None
            logger.info("TxMempool stopped.")

    def update(self):
        '''Mempool polling function.'''
        newstate = MempoolState(*proxy.poll_mempool())
        if newstate.height > self.state.height:
            self.blockqueue.put(
                (self.state, newstate, int(time())))
        self.state = newstate
        return newstate

    def blockworker_target(self):
        '''Target function for a worker thread that processes new memblocks.'''
        while True:
            args = self.blockqueue.get()
            if args == self.WORKER_STOP:
                break
            self.process_blocks(*args)
        logger.info("Block worker received stop sentinel; thread complete.")

    def process_blocks(self, prevstate, newstate, blocktime):
        '''Called when block count has increased.

        Records the mempool entries in a MemBlock instance and writes to disk.
        entries is a dict that maps txids to MemEntry objects.
        new_entries_ids is the set of txids in the mempool immediately after
        the block(s).
        '''
        memblocks = []
        prev_entries = prevstate.get_entries()
        for height in range(prevstate.height+1, newstate.height+1):
            block = proxy.getblock(proxy.getblockhash(height))
            memblock = MemBlock()
            memblock.record_block(height, blocktime, block, prev_entries)
            memblocks.append(memblock)

        # The set of transactions that were removed from the mempool, yet
        # were not included in a block.
        newstate_txids = set(newstate.rawmempool)
        conflicts = set(prev_entries) - newstate_txids
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
            logger.info("process_blocks: {} conflicts ({} bytes) removed.".
                        format(len(conflicts), conflicts_size))
        if conflicts_size > 10000:
            # If many conflicts are removed, it can screw up the txsource
            # estimation; so log a warning.
            logger.warning("process_blocks: {} bytes of conflicts removed.".
                           format(conflicts_size))

        for memblock in memblocks:
            stats = memblock.calc_stranding_feerate(bootstrap=False)
            if stats:
                logger.info("Block {}: stranding feerate is {}".
                            format(memblock.height, stats['sfr']))
            if self.dbfile and self.is_alive():
                try:
                    memblock.write(self.dbfile, self.blocks_to_keep)
                except Exception:
                    logger.exception("MemBlock write/del exception.")

        return memblocks

    def get_status(self):
        raise NotImplementedError
        # # TODO: gotta tidy this up
        # runtime = int(time() - self.starttime)
        # # TODO: use self.best_height for this.
        # currheight = proxy.getblockcount()
        # numhistory = len(MemBlock.get_heights())
        # if self.rawmempool:
        #     mempool_status = 'running'
        # else:
        #     mempool_status = 'stopped'
        # status = {
        #     'runtime': runtime,
        #     'height': currheight,
        #     'numhistory': numhistory,
        #     'mempool': mempool_status}
        # return status

    def __nonzero__(self):
        return self.state is not None


class MempoolState(object):
    '''Current block height + rawmempool.

    Updated every poll_period by TxMempool thread.
    '''

    def __init__(self, height, rawmempool):
        self.height = height
        self.rawmempool = rawmempool

    def get_entries(self):
        entries = {txid: MemEntry.from_rawentry(rawentry)
                   for txid, rawentry in self.rawmempool.iteritems()}
        return entries

    def __nonzero__(self):
        return bool(self.height)


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

    def __init__(self):
        self.height = None
        self.size = None
        self.time = None
        self.entries = None

    def record_block(self, blockheight, blocktime, block, entries):
        '''Label the mempool entries based on the block data.

        We record various block statistics, and for each MemEntry we label
        inblock and leadtime. See MemEntry for more info.
        '''
        # TODO: add warning if measured time differs greatly from timestamp
        self.height = blockheight
        self.size = len(block.serialize())
        self.time = blocktime
        self.entries = deepcopy(entries)
        for entry in self.entries.values():
            entry.inblock = False
            entry.isconflict = False
            entry.leadtime = self.time - entry.time

        blocktxids = [b2lx(tx.GetHash()) for tx in block.vtx]
        entries_inblock = set(entries) & set(blocktxids)
        for txid in entries_inblock:
            self.entries[txid].inblock = True
            # Delete it, because entries will be used for the next block
            # if there are > 1 blocks in this update cycle.
            del entries[txid]

        incl_text = 'Block {}: {}/{} in mempool'.format(
            blockheight, len(entries_inblock), len(blocktxids)-1)
        logger.info(incl_text)

        # As a measure of our node's connectivity, we want to note the
        # ratio below. If it is low, it means that our node is not being
        # informed of many transactions.
        if len(blocktxids) > 1:
            incl_ratio = len(entries_inblock) / (len(blocktxids)-1)
            if incl_ratio < 0.9:
                logger.warning(incl_text)

    def calc_stranding_feerate(self, bootstrap=False):
        if not self:
            raise ValueError("Empty memblock.")
        txs = tx_preprocess(self)
        if txs:
            return calc_stranding_feerate(txs, bootstrap=bootstrap)
        return None

    def write(self, dbfile, blocks_to_keep):
        '''Write MemBlock to disk.

        blocks_to_keep specifies how many blocks of information should be
        retained. All MemBlocks older (with respect to this block) than
        blocks_to_keep will be deleted.
        '''
        if not self:
            raise ValueError("Failed write: empty memblock.")
        db = None
        try:
            db = sqlite3.connect(dbfile)
            for key, val in MEMBLOCK_TABLE_SCHEMA.items():
                db.execute('CREATE TABLE IF NOT EXISTS %s (%s)' %
                           (key, ','.join(val)))
            db.execute('CREATE INDEX IF NOT EXISTS heightidx '
                       'ON txs (blockheight)')
            db.execute('CREATE INDEX IF NOT EXISTS blocks_heightidx '
                       'ON blocks (height)')
            with db_lock:
                with db:
                    db.execute('INSERT INTO blocks VALUES (?,?,?)',
                               (self.height, self.size, self.time))
                    db.executemany(
                        'INSERT INTO txs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        [(self.height, txid) + entry._get_attr_tuple()
                         for txid, entry in self.entries.iteritems()])
                if blocks_to_keep > 0:
                    height_thresh = self.height - blocks_to_keep
                    with db:
                        db.execute('DELETE FROM blocks WHERE height<=?',
                                   (height_thresh,))
                        db.execute('DELETE FROM txs WHERE blockheight<=?',
                                   (height_thresh,))
        finally:
            if db is not None:
                db.close()

    @classmethod
    def read(cls, blockheight, dbfile=memblock_dbfile):
        '''Read MemBlock from disk.

        Returns the memblock with specified blockheight.
        Returns None if no record exists for that block.
        Raises one of the sqlite3 errors if there are other problems.
        '''
        if not os.path.exists(dbfile):
            return None
        db = None
        try:
            db = sqlite3.connect(dbfile)
            with db_lock:
                block = db.execute('SELECT size, time FROM blocks '
                                   'WHERE height=?',
                                   (blockheight,)).fetchall()
                txlist = db.execute('SELECT * FROM txs WHERE blockheight=?',
                                    (blockheight,)).fetchall()
        finally:
            if db is not None:
                db.close()
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

    @staticmethod
    def get_heights(blockrangetuple=None, dbfile=memblock_dbfile):
        '''Get the list of MemBlocks stored on disk.

        Returns a list of heights of all MemBlocks on disk within
        range(*blockrangetuple)
        '''
        if not os.path.exists(dbfile):
            return []
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
        finally:
            if db is not None:
                db.close()
        return [r[0] for r in heights]

    def __repr__(self):
        return "MemBlock{height: %d, size: %d, num entries: %d}" % (
            self.height, self.size, len(self.entries))

    def __nonzero__(self):
        return self.entries is not None

    def __eq__(self, other):
        if not isinstance(other, MemBlock):
            return False
        return self.__dict__ == other.__dict__


class MemEntry(SimEntry):
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

    def __init__(self):
        super(MemEntry, self).__init__(None, None)

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
        '''Return MemEntry from attribute tuple.

        Used when reading MemBlock from disk.
        '''
        entry = cls()

        (
            entry.size,
            entry.fee,
            entry.startingpriority,
            entry.currentpriority,
            entry.time,
            entry.height,
            entry.depends,
            entry.feerate,
            entry.leadtime,
            entry.isconflict,
            entry.inblock
        ) = tup

        entry.fee = decimal.Decimal(entry.fee)
        entry.currentpriority = decimal.Decimal(entry.currentpriority)
        entry.startingpriority = decimal.Decimal(entry.startingpriority)
        entry.depends = entry.depends.split(',') if entry.depends else []
        entry.isconflict = bool(entry.isconflict)
        entry.inblock = bool(entry.inblock)

        return entry

    @classmethod
    def from_rawentry(cls, rawentry):
        '''Return MemEntry from rawmempool dict.

        rawentry is a value in the dict returned by
        proxy.getrawmempool(verbose=True).
        '''
        entry = cls()
        entry.size = rawentry['size']
        entry.fee = rawentry['fee']
        entry.startingpriority = rawentry['startingpriority']
        entry.currentpriority = rawentry['currentpriority']
        entry.time = rawentry['time']
        entry.height = rawentry['height']
        entry.depends = rawentry['depends'][:]

        # Additional fields
        entry.feerate = get_feerate(rawentry)
        entry.leadtime = None
        entry.isconflict = None
        entry.inblock = None

        return entry

    def __repr__(self):
        return str(self.__dict__)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


# TODO: maybe deprecate this?
def get_mempool_size(minfeerate):
    '''Get size of mempool.

    Returns size of mempool in bytes for all transactions that have a feerate
    >= minfeerate.
    '''
    rawmempool = proxy.getrawmempool(verbose=True)
    txs = [MemEntry.from_rawentry(entry) for entry in rawmempool.values()]
    return sum([tx.size for tx in txs if tx.feerate >= minfeerate])


def get_mempool():
    rawmempool = proxy.getrawmempool(verbose=True)
    return {txid: MemEntry.from_rawentry(entry)
            for txid, entry in rawmempool.items()}
