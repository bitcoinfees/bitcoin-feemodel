from __future__ import division

import logging
from time import time
from itertools import groupby

from tabulate import tabulate

from feemodel.config import knownpools, memblock_dbfile
from feemodel.util import get_coinbase_info, get_block_timestamp
from feemodel.util import get_block_size, get_hashesperblock
from feemodel.stranding import tx_preprocess, calc_stranding_feerate
from feemodel.simul import SimPool, SimPools
from feemodel.txmempool import MemBlock

logger = logging.getLogger(__name__)

MAX_TXS = 10000


class PoolEstimate(SimPool):

    def __init__(self, blockheights, hashrate, maxblocksize):
        self.blockheights = blockheights
        self.hashrate = hashrate
        self.feelimitedblocks = None
        self.sizelimitedblocks = None
        self.mfrstats = None
        super(PoolEstimate, self).__init__(
            hashrate, maxblocksize, float("inf"))

    def estimate_minfeerate(self, stopflag=None, dbfile=memblock_dbfile):
        txs = []
        self.feelimitedblocks = []
        self.sizelimitedblocks = []

        for height in sorted(self.blockheights, reverse=True):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            if block is None:
                continue
            _inblocktxs = filter(lambda tx: tx.inblock, block.entries.values())
            if _inblocktxs:
                avgtxsize = (
                    sum([tx.size for tx in _inblocktxs]) / len(_inblocktxs))
            else:
                avgtxsize = 0.
            # We assume a block is fee-limited if its size is smaller than
            # the maxblocksize, minus a margin of the block avg tx size.
            if self.maxblocksize - block.blocksize > avgtxsize:
                self.feelimitedblocks.append(
                    (block.blockheight, block.blocksize))
                txs.extend(tx_preprocess(block))
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
                self.sizelimitedblocks.append(
                    (block.blockheight, block.blocksize))

        if not txs and self.sizelimitedblocks:
            # All the blocks are close to the max block size.
            # This should happen rarely, so we just choose the smallest block.
            smallestheight = min(self.sizelimitedblocks, key=lambda x: x[1])[0]
            block = MemBlock.read(smallestheight, dbfile=dbfile)
            if block:
                txs.extend(tx_preprocess(block))

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

        numblocks = len(self.feelimitedblocks) + len(self.sizelimitedblocks)
        maxblocks = len(self.blockheights)

        if numblocks < maxblocks:
            logger.warning("MFR estimation: only %d memblocks found out "
                           "of possible %d" % (numblocks, maxblocks))


class PoolsEstimator(SimPools):

    def __init__(self):
        self.pools = {}
        self.blockrate = None
        self.blockmap = {}
        self.poolinfo = knownpools
        self.timestamp = 0.

    def update(self):
        super(PoolsEstimator, self).update(self.pools)

    def start(self, blockrangetuple, stopflag=None, dbfile=memblock_dbfile):
        logger.info("Beginning pool estimation "
                    "from blockrange({}, {})".format(*blockrangetuple))
        starttime = time()
        self.id_blocks(blockrangetuple, stopflag=stopflag)
        self.estimate_pools(stopflag=stopflag, dbfile=dbfile)
        self.calc_blockrate()
        self.timestamp = starttime
        logger.info("Finished pool estimation in %.2f seconds." %
                    (time()-starttime))

    def id_blocks(self, blockrangetuple, stopflag=None):
        for height in range(*blockrangetuple):
            if height in self.blockmap:
                continue
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            try:
                baddrs, btag = get_coinbase_info(height)
                blocksize = get_block_size(height)
                numhashes = get_hashesperblock(height)
            except IndexError:
                raise IndexError("Bad block range.")

            name = None
            for paddr, pattrs in self.poolinfo['payout_addresses'].items():
                candidate_name = pattrs['name']
                if paddr in baddrs:
                    if name is None:
                        name = candidate_name
                    elif name != candidate_name:
                        logger.warning("> 1 pools mapped to block %d" % height)

            for ptag, pattrs in self.poolinfo['coinbase_tags'].items():
                candidate_name = pattrs['name']
                if ptag in btag:
                    if name is None:
                        name = candidate_name
                    elif name != candidate_name:
                        logger.warning("> 1 pools mapped to block %d" % height)

            if name is None:
                for addr in baddrs:
                    if addr is not None:
                        # Underscore indicates that the pool is not in the
                        # list of known pools. We use the first valid
                        # coinbase addr as the name.
                        name = addr[:12] + '_'
                        break

            if name is None:
                logger.warning(
                    "Unable to identify pool of block %d." % height)
                name = 'U' + str(height) + '_'

            self.blockmap[height] = (name, blocksize, numhashes)

        for height in self.blockmap.keys():
            if height < blockrangetuple[0] or height >= blockrangetuple[1]:
                del self.blockmap[height]

        if not self.blockmap:
            raise ValueError("Empty block range.")

        logger.info("Finished identifying blocks.")

    def estimate_pools(self, stopflag=None, dbfile=memblock_dbfile):
        if len(self.blockmap) < 2:
            raise ValueError("Not enough blocks.")
        self.pools = {}
        _windowstart = get_block_timestamp(max(self.blockmap))
        _windowend = get_block_timestamp(min(self.blockmap))
        windowlen = _windowstart - _windowend

        def poolname_keyfn(blocktuple):
            '''Select the pool name for itertools.groupby.'''
            return blocktuple[1][0]

        blockmap_items = sorted(self.blockmap.items(), key=poolname_keyfn)
        for poolname, items_namegroup in groupby(
                blockmap_items, poolname_keyfn):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            blockheights = []
            blocksizes = []
            totalhashes = 0.
            for height, (name, blocksize, numhashes) in items_namegroup:
                blockheights.append(height)
                blocksizes.append(blocksize)
                totalhashes += numhashes
            maxblocksize = max(blocksizes)
            hashrate = totalhashes / windowlen
            pool = PoolEstimate(blockheights, hashrate, maxblocksize)
            pool.estimate_minfeerate(stopflag=stopflag, dbfile=dbfile)
            logger.debug("Estimated %s: %s" % (poolname, repr(pool)))
            self.pools[poolname] = pool

    def calc_blockrate(self, height=None):
        if not height:
            height = max(self.blockmap)
        totalhashrate = self.calc_totalhashrate()
        if not totalhashrate:
            raise ValueError("No pools.")
        curr_hashesperblock = get_hashesperblock(height)
        self.blockrate = totalhashrate / curr_hashesperblock

    def print_pools(self):
        poolitems = sorted(self.pools.items(),
                           key=lambda poolitem: poolitem[1].hashrate,
                           reverse=True)
        totalhashrate = self.calc_totalhashrate()
        if not totalhashrate:
            print("No pools.")
            return
        headers = ["Name", "Hashrate (Thps)", "Prop", "MBS", "MFR",
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
        print(tabulate(table, headers=headers))
        print("Avg block interval is %.2f" % (1./self.blockrate,))
        print("Total hashrate is {} Thps.".format(totalhashrate*1e-12))
