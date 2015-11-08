from __future__ import division

import logging
from time import time
from math import ceil
from collections import defaultdict
from operator import attrgetter, itemgetter

from tabulate import tabulate

import feemodel.config
from feemodel.config import MINRELAYTXFEE, DIFF_RETARGET_INTERVAL
from feemodel.util import (get_block_timestamp, get_hashesperblock,
                           BlockMetadata)
from feemodel.stranding import tx_preprocess, calc_stranding_feerate
from feemodel.simul.pools import SimPool, SimPools, SimPoolsNP
from feemodel.txmempool import MemBlock, MEMBLOCK_DBFILE

logger = logging.getLogger(__name__)

MAX_TXS = 50000


class PoolsEstimatorNP(SimPoolsNP):

    def __init__(self):
        super(PoolsEstimatorNP, self).__init__(None, None, blockrate=None)
        self.blockstats = {}

    def start(self, blockrangetuple, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        logger.info("Beginning NP pool estimation "
                    "from blockrange({}, {})".format(*blockrangetuple))
        self._clear_window(blockrangetuple[0])
        for height in range(*blockrangetuple):
            if height in self.blockstats:
                continue
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            memblock = MemBlock.read(height, dbfile=dbfile)
            if memblock is None:
                continue
            self.update(memblock, is_init=True)
        self._calc_estimates()
        logger.info("Finished NP pool estimation.")

    def update(self, memblock, is_init=False, windowsize=None):
        sfr_stats = memblock.calc_stranding_feerate()
        if sfr_stats['altbiasref'] == MINRELAYTXFEE:
            sfr = MINRELAYTXFEE
        else:
            sfr = sfr_stats['sfr']
        mempoolsize = sum(
            [entry.size for entry in memblock.entries.values()
             if entry.feerate >= MINRELAYTXFEE])
        self.blockstats[memblock.blockheight] = (
            mempoolsize, memblock.time, memblock.blocksize, sfr)
        if windowsize:
            height_thresh = memblock.blockheight - windowsize + 1
            self._clear_window(height_thresh)
        if not is_init:
            self._calc_estimates()

    def _clear_window(self, height_thresh):
        for height in self.blockstats.keys():
            if height < height_thresh:
                del self.blockstats[height]

    def _calc_estimates(self, blockmingap=300, tailpct=0.1):
        if len(self.blockstats) < 2:
            # TODO: better checks, taking into consideration tailpct
            # raise ValueError("Not enough blocks.")
            return
        startheight = min(self.blockstats)
        endheight = max(self.blockstats)

        # Calculate blockrate
        numhashes = None
        totalhashes = 0
        for height in range(startheight, endheight+1):
            if numhashes is None or not height % DIFF_RETARGET_INTERVAL:
                numhashes = get_hashesperblock(height)
            totalhashes += numhashes
        starttime = self.blockstats[startheight][1]
        endtime = self.blockstats[endheight][1]
        hashrate = totalhashes / (endtime - starttime)
        self.blockrate = hashrate / numhashes

        prevtime = None
        prevheight = None
        blockstats = []
        for height, blockstat in sorted(self.blockstats.items()):
            if prevheight is not None and (
                    height == prevheight + 1 and
                    blockstat[1] > prevtime + blockmingap):
                blockstats.append(blockstat)
            prevtime = blockstat[1]
            prevheight = height

        if not blockstats:
            # This really shouldn't happen at all.
            raise ValueError("Not enough blocks.")

        # Calculate minfeerates and maxblocksizes
        blockstats.sort()
        # minfeerates are from the lower t percent of the blocks
        tailidx = int(ceil(tailpct*len(blockstats)))
        self.minfeerates = map(itemgetter(3), blockstats[:tailidx])
        self.maxblocksizes = map(itemgetter(2), blockstats[-tailidx:])
        return blockstats

    def __str__(self):
        try:
            self.check()
        except ValueError as e:
            return e.message
        minfeerates = sorted(self.minfeerates)
        maxblocksizes = sorted(self.maxblocksizes)
        s = ("minfeerates: {}\nmaxblocksizes: {}\nblockinterval: {}".
             format(minfeerates, maxblocksizes, 1 / self.blockrate))
        return s


class PoolEstimate(SimPool):

    def __init__(self):
        self.blocks = []
        self.mfrstats = None
        super(PoolEstimate, self).__init__(None, None, float("inf"))

    def estimate(self, windowlen, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        totalhashes = sum([block.hashes for block in self.blocks])
        self.hashrate = totalhashes / windowlen
        self.maxblocksize = max(map(attrgetter("size"), self.blocks))

        txs = []
        feelimitedblocks = []
        sizelimitedblocks = []
        for blockmeta in self.blocks:
            # We assume a block is fee-limited if its size is more than 10 kB
            # smaller than the max block size.
            # TODO: find a better way of choosing the margin size.
            if self.maxblocksize - blockmeta.size > 10000:
                feelimitedblocks.append(blockmeta)
            else:
                sizelimitedblocks.append(blockmeta)

        if feelimitedblocks:
            # For minfeerate estimation, prioritize medium-sized
            # and recent blocks.
            # We should avoid both small blocks (where minblocksize
            # and blockprioritysize effects may be in play) and large blocks
            # (max block size may have been reached). Separating
            # feelimitedblocks and sizelimitedblocks works most of the time,
            # however when pools are changing their max block size policy,
            # it would lead to inaccurate results.
            # Prioritizing recent blocks helps in the case where pools are
            # changing their minfeerate policy.
            meanblocksize = (
                sum(map(attrgetter("size"), feelimitedblocks)) /
                len(feelimitedblocks))
            blockscores = [[block, 0] for block in feelimitedblocks]
            blockscores.sort(key=lambda b: abs(b[0].size-meanblocksize))
            for idx, blockscore in enumerate(blockscores):
                blockscore[1] = max(idx, blockscore[1])
            blockscores.sort(key=lambda b: b[0].height, reverse=True)
            for idx, blockscore in enumerate(blockscores):
                blockscore[1] = max(idx, blockscore[1])
            blockscores.sort(key=itemgetter(1))
            feelimitedblocks, dummy = zip(*blockscores)

        nummissingblocks = 0
        for blockmeta in feelimitedblocks:
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            memblock = MemBlock.read(blockmeta.height, dbfile=dbfile)
            if memblock is None:
                nummissingblocks += 1
                continue
            txs.extend(tx_preprocess(memblock))
            # Only take up to MAX_TXS txs.
            # The optimal figure will depend on the tx byte rate profile:
            # are there sufficient transactions with a feerate close to
            # the pool's minfeerate? In the future MAX_TXS could be selected
            # automatically.
            if len(txs) >= MAX_TXS:
                break

        if not txs and sizelimitedblocks:
            # All the blocks are close to the max block size.
            # This should happen rarely, so we just choose the smallest block.
            smallestblock = min(sizelimitedblocks, key=attrgetter("size"))
            memblock = MemBlock.read(smallestblock.height, dbfile=dbfile)
            if memblock:
                txs.extend(tx_preprocess(memblock))

        if txs:
            self.mfrstats = calc_stranding_feerate(txs)
            self.minfeerate = self.mfrstats['sfr']
        else:
            logger.warning("Pool estimation: no valid transactions.")
            self.mfrstats = {
                "sfr": float("inf"),
                "bias": float("inf"),
                "mean": float("inf"),
                "std": float("inf"),
                "abovekn": (-1, -1),
                "belowkn": (-1, -1),
            }

        if nummissingblocks:
            logger.warning("MFR estimation: {} missing blocks.".
                           format(nummissingblocks))

    def __and__(self, other):
        """Check if there is output address overlap."""
        # Only use addrs that are not None.  Recall that None addr means that
        # there was an error raised by CBitcoinAddress.from_scriptPubKey.
        selfaddrs = sum(map(attrgetter("addrs"), self.blocks), [])
        otheraddrs = sum(map(attrgetter("addrs"), other.blocks), [])
        selfaddrs_notnone = filter(bool, selfaddrs)
        otheraddrs_notnone = filter(bool, otheraddrs)
        return bool(set(selfaddrs_notnone) & set(otheraddrs_notnone))

    def __add__(self, other):
        """Add the blocks of other to the blocks of self."""
        self.blocks.extend(other.blocks)

    def get_addresses(self):
        "Get the coinbase output addresses of blocks by this pool."
        return set(sum([b.addrs for b in self.blocks], []))


class PoolsEstimator(SimPools):

    def __init__(self):
        self.pools = {}
        self.blockrate = None
        self.blocksmetadata = {}
        self.timestamp = 0.

    def start(self, blockrangetuple, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        logger.info("Beginning pool estimation "
                    "from blockrange({}, {})".format(*blockrangetuple))
        self.timestamp = time()
        self.get_blocksmetadata(blockrangetuple, stopflag=stopflag)
        self.clusterpools()
        self.estimate_pools(stopflag=stopflag, dbfile=dbfile)
        self.calc_blockrate()
        logger.info("Finished pool estimation in %.2f seconds." %
                    (time()-self.timestamp))

    def get_blocksmetadata(self, blockrangetuple, stopflag=None):
        for height in range(*blockrangetuple):
            if height in self.blocksmetadata:
                continue
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            self.blocksmetadata[height] = BlockMetadata(height)

        # Remove blocks outside the specified range
        for height in self.blocksmetadata.keys():
            if not blockrangetuple[0] <= height < blockrangetuple[1]:
                del self.blocksmetadata[height]

        logger.info("Finished getting block metadata.")

    def clusterpools(self):
        if not self.blocksmetadata:
            raise ValueError("Empty block range.")

        pooltags = feemodel.config.pooltags
        pools = defaultdict(PoolEstimate)
        for block in self.blocksmetadata.values():
            pools[block.get_poolname()].blocks.append(block)

        still_clustering = True
        while still_clustering:
            still_clustering = False
            for poolname, pool in pools.items():
                if poolname in pooltags:
                    continue
                for hostpool in pools.values():
                    if hostpool is not pool and hostpool & pool:
                        hostpool + pool
                        del pools[poolname]
                        still_clustering = True
                        break

        newnames = set(pools) - set(self.pools)
        for name in newnames:
            logger.info("New pool name: {}".format(name))
        self.pools = dict(pools)

    def estimate_pools(self, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        if len(self.blocksmetadata) < 2:
            raise ValueError("Not enough blocks.")
        _windowend = get_block_timestamp(max(self.blocksmetadata))
        _windowstart = get_block_timestamp(min(self.blocksmetadata))
        windowlen = _windowend - _windowstart
        for name, pool in self.pools.items():
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            pool.estimate(windowlen, stopflag=stopflag, dbfile=dbfile)
            logger.debug("Estimated {}: {}".format(name, repr(pool)))

    def calc_blockrate(self, height=None):
        if not height:
            height = max(self.blocksmetadata)
        totalhashrate = self.calc_totalhashrate()
        if not totalhashrate:
            raise ValueError("No pools.")
        curr_hashesperblock = get_hashesperblock(height)
        self.blockrate = totalhashrate / curr_hashesperblock

    def __str__(self):
        try:
            self.check()
        except ValueError as e:
            return e.message
        poolitems = sorted(self.pools.items(),
                           key=lambda pitem: pitem[1].hashrate, reverse=True)
        totalhashrate = self.calc_totalhashrate()
        headers = ["Name", "HR (Thps)", "Prop", "MBS", "MFR",
                   "AKN", "BKN", "MFR.mean", "MFR.std", "MFR.bias"]
        table = [[
            name,
            pool.hashrate*1e-12,
            pool.hashrate/totalhashrate,
            pool.maxblocksize,
            pool.minfeerate,
            pool.mfrstats['abovekn'],
            pool.mfrstats['belowkn'],
            pool.mfrstats['mean'],
            pool.mfrstats['std'],
            pool.mfrstats['bias']]
            for name, pool in poolitems]
        poolstats = tabulate(table, headers=headers)
        meanblocksize = sum([row[2]*row[3] for row in table])
        maxcap = meanblocksize*self.blockrate

        table = [
            ("Block interval (s)", 1 / self.blockrate),
            ("Total hashrate (Thps)", totalhashrate*1e-12),
            ("Max capacity (bytes/s)", maxcap)
        ]
        miscstats = tabulate(table)
        return poolstats + '\n' + miscstats
