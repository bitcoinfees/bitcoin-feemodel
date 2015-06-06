from __future__ import division

import os
import threading
import sqlite3
import decimal
import logging
from time import time
from copy import copy
from itertools import groupby
from operator import attrgetter

from bitcoin.core import b2lx

from feemodel.config import config, datadir, MINRELAYTXFEE, PRIORITYTHRESH
from feemodel.util import (proxy, StoppableThread, get_feerate, WorkerThread,
                           cumsum_gen, BlockMetadata, StepFunction)
from feemodel.stranding import tx_preprocess, calc_stranding_feerate
from feemodel.simul.simul import SimEntry

logger = logging.getLogger(__name__)
db_lock = threading.Lock()

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
MEMBLOCK_DBFILE = os.path.join(datadir, 'memblock.db')


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

    def __init__(self, dbfile=MEMBLOCK_DBFILE,
                 blocks_to_keep=config.getint("txmempool", "blocks_to_keep"),
                 poll_period=config.getfloat("txmempool", "poll_period")):
        self.state = None
        self.blockworker = None
        self.dbfile = dbfile
        self.blocks_to_keep = blocks_to_keep
        self.poll_period = poll_period
        super(TxMempool, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        """Target function of the thread.

        Updates mempool every poll_period seconds.
        """
        logger.info("Starting TxMempool with {} blocks_to_keep.".
                    format(self.blocks_to_keep))
        logger.info("memblock dbfile is at {}".format(self.dbfile))
        self.blockworker = WorkerThread(self.process_blocks)
        self.blockworker.start()
        try:
            self.state = get_mempool_state()
            while not self.is_stopped():
                self.update()
                self.sleep(self.poll_period)
        finally:
            self.blockworker.stop()
            self.state = None
            logger.info("TxMempool stopped.")

    def update(self):
        """Update the mempool state.

        If block height has increased, call self.process_blocks through
        blockworker thread.
        """
        newstate = get_mempool_state()
        if newstate.height > self.state.height:
            self.blockworker.put(self.state, newstate)
        self.state = newstate
        logger.debug(repr(newstate))
        return newstate

    def process_blocks(self, prevstate, newstate):
        """Record the mempool state in a MemBlock.

        This is called in self.blockworker.run.
        """
        # Make a copy because we are going to mutate it
        prevstate = copy(prevstate)
        memblocks = []
        while prevstate.height < newstate.height:
            memblock = MemBlock()
            memblock.record_block(prevstate)
            memblocks.append(memblock)

        # The set of transactions that were removed from the mempool, yet
        # were not included in a block.
        conflicts = (prevstate - newstate).entries
        conflicts_size = sum([entry.size for entry in conflicts.values()])
        for txid in conflicts:
            # For the first block, label the MemBlock entries that are
            # conflicts. Assume the conflict was removed after the first
            # block, so remove them from the remaining blocks.
            memblocks[0].entries[txid].isconflict = True
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

        if self.dbfile and self.is_alive():
            for memblock in memblocks:
                try:
                    memblock.write(self.dbfile, self.blocks_to_keep)
                except Exception:
                    logger.exception("MemBlock write/del exception.")

        return memblocks

    def get_stats(self):
        stats = {
            "params": {
                "poll_period": self.poll_period,
                "blocks_to_keep": self.blocks_to_keep
            },
            "num_memblocks": len(MemBlock.get_heights())
        }
        state = self.state
        if state is not None:
            stats.update(state.get_stats())
        return stats

    def __nonzero__(self):
        return self.state is not None


class MempoolState(object):
    """Mempool state.

    Comprised of:
        height - the block height
        entries - dictionary of mempool entries
        time - time in seconds
    """

    def __init__(self, height, rawmempool):
        self.height = height
        self.entries = {txid: MemEntry.from_rawentry(rawentry)
                        for txid, rawentry in rawmempool.iteritems()}
        self.time = int(time())

    def get_sizefn(self):
        entries = sorted(self.entries.values(), key=attrgetter("feerate"),
                         reverse=True)
        sizebyfee = [
            (feerate, sum([entry.size for entry in feegroup]))
            for feerate, feegroup in groupby(entries, attrgetter("feerate"))]
        if not sizebyfee:
            return StepFunction([0, 1], [0, 0])
        feerates_rev, sizes = zip(*sizebyfee)
        cumsize_rev = list(cumsum_gen(sizes))
        feerates = list(reversed(feerates_rev))
        cumsize = list(reversed(cumsize_rev))
        sizefn = StepFunction(feerates, cumsize)
        sizefn.addpoint(feerates[-1]+1, 0)
        return sizefn

    def get_stats(self):
        sizefn = self.get_sizefn()
        approxfn = sizefn.approx()
        feerates_approx, cumsize_approx = zip(*approxfn)
        size_with_fee = sizefn(MINRELAYTXFEE)

        stats = {
            "cumsize": {
                "feerates": feerates_approx,
                "size": cumsize_approx,
            },
            "currheight": self.height,
            "numtxs": len(self.entries),
            "sizewithfee": size_with_fee
        }
        return stats

    def __copy__(self):
        cpy = MempoolState(self.height, {})
        cpy.entries = {txid: copy(entry)
                       for txid, entry in self.entries.iteritems()}
        cpy.time = self.time
        return cpy

    def __sub__(self, other):
        if not isinstance(other, MempoolState):
            raise TypeError("Operands must both be MempoolState instances.")
        result = MempoolState(self.height - other.height, {})
        result.time = self.time - other.time
        result.entries = {
            txid: self.entries[txid]
            for txid in set(self.entries) - set(other.entries)
        }
        return result

    def __repr__(self):
        return "MempoolState(height: {}, entries: {}, time: {})".format(
            self.height, len(self.entries), self.time)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return self.__dict__ != other.__dict__


class MemBlock(MempoolState):
    '''The mempool state at the time a block was discovered.'''

    def __init__(self):
        # The attributes inherited from MempoolState
        self.height = None
        self.entries = None
        self.time = None

        # MemBlock specific attributes
        self.blockheight = None
        self.blocksize = None

    def record_block(self, state):
        self.height = state.height
        self.entries = {txid: copy(entry)
                        for txid, entry in state.entries.iteritems()}
        self.time = state.time
        for entry in self.entries.values():
            entry.inblock = False
            entry.isconflict = False
            entry.leadtime = self.time - entry.time

        self.blockheight = state.height + 1
        block = proxy.getblock(proxy.getblockhash(self.blockheight))
        self.blocksize = len(block.serialize())
        blockname = BlockMetadata(self.blockheight).get_poolname()

        blocktxids = [b2lx(tx.GetHash()) for tx in block.vtx]
        entries_inblock = set(self.entries) & set(blocktxids)
        for txid in entries_inblock:
            self.entries[txid].inblock = True
            # Delete it, because state.entries will be used for the next block
            # if there are > 1 blocks in this update cycle.
            del state.entries[txid]

        stats = self.calc_stranding_feerate(bootstrap=False)
        stranding_feerate = stats['sfr'] if stats else None

        blocktext = (
            'Block {} ({} bytes) by {}: {}/{} in mempool, SFR is {}.'.
            format(self.blockheight, self.blocksize, blockname,
                   len(entries_inblock), len(blocktxids)-1, stranding_feerate))
        logger.info(blocktext)

        # As a measure of our node's connectivity, we want to note the
        # ratio below. If it is low, it means that our node is not being
        # informed of many transactions.
        if len(blocktxids) > 1:
            incl_ratio = len(entries_inblock) / (len(blocktxids)-1)
            if incl_ratio < 0.9:
                logger.warning("Only {}/{} in block {}.".format(
                               len(entries_inblock), len(blocktxids)-1,
                               self.blockheight))

        state.height += 1

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
                    db.execute(
                        'INSERT INTO blocks VALUES (?,?,?)',
                        (self.blockheight, self.blocksize, self.time))
                    db.executemany(
                        'INSERT INTO txs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        [(self.blockheight, txid) + entry._get_attr_tuple()
                         for txid, entry in self.entries.iteritems()])
                if blocks_to_keep > 0:
                    height_thresh = self.blockheight - blocks_to_keep
                    with db:
                        db.execute('DELETE FROM blocks WHERE height<=?',
                                   (height_thresh,))
                        db.execute('DELETE FROM txs WHERE blockheight<=?',
                                   (height_thresh,))
        finally:
            if db is not None:
                db.close()

    @classmethod
    def read(cls, blockheight, dbfile=MEMBLOCK_DBFILE):
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
        memblock.height = blockheight - 1
        memblock.entries = {
            tx[1]: MemEntry._from_attr_tuple(tx[2:]) for tx in txlist}
        memblock.time = blocktime

        memblock.blockheight = blockheight
        memblock.blocksize = blocksize
        return memblock

    @staticmethod
    def get_heights(blockrangetuple=None, dbfile=MEMBLOCK_DBFILE):
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

    def __nonzero__(self):
        return self.entries is not None

    def __repr__(self):
        return "MemBlock(blockheight: %d, blocksize: %d, len(entries): %d)" % (
            self.blockheight, self.blocksize, len(self.entries))

    def __copy__(self):
        raise NotImplementedError


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
        self.fee = None
        self.startingpriority = None
        self.currentpriority = None
        self.time = None
        self.height = None
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
        return (self.currentpriority > PRIORITYTHRESH or
                self.feerate < MINRELAYTXFEE)

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
        for attr in rawentry:
            setattr(entry, attr, rawentry[attr])
        entry.depends = entry.depends[:]
        entry.feerate = get_feerate(rawentry)
        return entry

    def __copy__(self):
        cpy = MemEntry()
        for attr in self.__dict__:
            setattr(cpy, attr, getattr(self, attr))
        cpy.depends = cpy.depends[:]
        return cpy

    def __repr__(self):
        return str(self.__dict__)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return self.__dict__ != other.__dict__


def get_mempool_state():
    return MempoolState(*proxy.poll_mempool())
