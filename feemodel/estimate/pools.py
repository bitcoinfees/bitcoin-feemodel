from __future__ import division

import logging
from time import time
from collections import defaultdict

from tabulate import tabulate

import feemodel.config
from feemodel.util import (get_block_timestamp, get_block_size,
                           get_coinbase_info, get_hashesperblock)
from feemodel.stranding import tx_preprocess, calc_stranding_feerate
from feemodel.simul import SimPool, SimPools
from feemodel.txmempool import MemBlock, MEMBLOCK_DBFILE

logger = logging.getLogger(__name__)

MAX_TXS = 20000


class BlockMetadata(object):
    """Contains info relevant to pool clustering."""

    def __init__(self, height):
        self.height = height
        self.addrs, self.tag = get_coinbase_info(height)
        self.size = get_block_size(height)
        self.hashes = get_hashesperblock(height)

    def __and__(self, other):
        return set(self.addrs) & set(other.addrs)

    def __repr__(self):
        return ("BlockMetaData(height: {}, size: {})".
                format(self.height, self.size))


class PoolEstimate(SimPool):

    def __init__(self):
        self.blocks = []
        self.feelimitedblocks = None
        self.sizelimitedblocks = None
        self.mfrstats = None
        super(PoolEstimate, self).__init__(None, None, float("inf"))

    def estimate(self, windowlen, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        totalhashes = sum([block.hashes for block in self.blocks])
        self.hashrate = totalhashes / windowlen
        self.maxblocksize = max([block.size for block in self.blocks])

        txs = []
        self.feelimitedblocks = []
        self.sizelimitedblocks = []

        nummissingblocks = 0
        # for height in sorted(self.blockheights, reverse=True):
        for blockmeta in sorted(self.blocks,
                                key=lambda b: b.height, reverse=True):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            height = blockmeta.height
            memblock = MemBlock.read(height, dbfile=dbfile)
            if memblock is None:
                nummissingblocks += 1
                continue
            _inblocktxs = filter(lambda tx: tx.inblock,
                                 memblock.entries.values())
            if _inblocktxs:
                avgtxsize = (
                    sum([tx.size for tx in _inblocktxs]) / len(_inblocktxs))
            else:
                avgtxsize = 0.
            # We assume a block is fee-limited if its size is smaller than
            # the maxblocksize, minus a margin of 3 times the blk avg tx size.
            if self.maxblocksize - memblock.blocksize > 3*avgtxsize:
                self.feelimitedblocks.append(blockmeta)
                txs.extend(tx_preprocess(memblock))
                # Only take up to MAX_TXS of the most recent transactions.
                # If MAX_TXS is sufficiently high, this helps the adaptivity
                # of the estimation (i.e. react more quickly to changes in
                # the pool's minfeerate, at a small cost to the estimate
                # precision). The optimal figure will depend on the tx byte
                # rate profile: are there sufficient transactions with a
                # feerate close to the pool's minfeerate? In the future
                # MAX_TXS could be selected automatically.
                if len(txs) >= MAX_TXS:
                    break
            else:
                self.sizelimitedblocks.append(blockmeta)

        if not txs and self.sizelimitedblocks:
            # All the blocks are close to the max block size.
            # This should happen rarely, so we just choose the smallest block.
            smallestheight = min(
                self.sizelimitedblocks, key=lambda b: b.size).height
            memblock = MemBlock.read(smallestheight, dbfile=dbfile)
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

    def get_addresses(self):
        "Get the coinbase output addresses of blocks by this pool."
        return set(sum([b.addrs for b in self.blocks], []))


class PoolsEstimator(SimPools):

    def __init__(self):
        self.pools = {}
        self.blockrate = None
        self.blocksmetadata = {}
        self.timestamp = 0.

    def update(self):
        super(PoolsEstimator, self).update(self.pools)

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

        clusters = []
        # Cluster by address
        for block in self.blocksmetadata.values():
            matched_existing = False
            for cluster in clusters:
                if any([self.blocksmetadata[height] & block
                        for height in cluster]):
                    cluster.add(block.height)
                    matched_existing = True
                    break
            if not matched_existing:
                clusters.append(set((block.height,)))

        # Group clusters by tags
        pooltags = feemodel.config.pooltags
        pools = defaultdict(PoolEstimate)
        for idx, cluster in enumerate(clusters):
            assigned_name = None
            for name, taglist in pooltags.items():
                num_tag_match = sum([
                    any([tag in self.blocksmetadata[height].tag
                         for tag in taglist])
                    for height in cluster])
                match_proportion = num_tag_match / len(cluster)
                if match_proportion > 0.5:
                    # More than half the blocks in the cluster have at least
                    # one of the tags in taglist as a substring
                    if not assigned_name:
                        assigned_name = name
                    else:
                        # A cluster is assigned to two separate names.
                        # This means something weird is happening, so don't
                        # assign it to any known name at all.
                        logger.error(
                            "Cluster {} assigned to names {} and {}.".
                            format(idx, name, assigned_name))
                        assigned_name = None
                        break
            if assigned_name is None:
                clusterblocks = [self.blocksmetadata[height]
                                 for height in cluster]
                addrs = sorted(sum([b.addrs for b in clusterblocks], []))
                for addr in addrs:
                    if addr is not None:
                        assigned_name = addr[:12] + "_"
                        break
            if assigned_name is None:
                logger.warning("No name for cluster {}.".format(idx))
                assigned_name = "Cluster" + str(idx)
            clusterblocks = [self.blocksmetadata[height] for height in cluster]
            pools[assigned_name].blocks.extend(clusterblocks)

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
