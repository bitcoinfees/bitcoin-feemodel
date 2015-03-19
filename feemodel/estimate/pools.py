from __future__ import division

import logging
from time import time
from itertools import groupby
from feemodel.config import knownpools, history_file
from feemodel.util import get_coinbase_info, Table, get_block_timestamp
from feemodel.util import get_pph, get_block_size
from feemodel.stranding import tx_preprocess, calc_stranding_feerate
from feemodel.simul import SimPool, SimPools
from feemodel.txmempool import MemBlock

logger = logging.getLogger(__name__)


class PoolEstimate(SimPool):
    def __init__(self, blockheights, blockrangetuple, maxblocksize):
        self.blockheights = blockheights
        self.hashrate = None
        self.feelimitedblocks = None
        self.sizelimitedblocks = None
        self.stats = None
        self._estimate_hashrate(blockrangetuple)
        super(PoolEstimate, self).__init__(
            self.hashrate, maxblocksize, float("inf"))

    def estimate_params(self, stopflag=None, dbfile=history_file):
        txs = []
        self.feelimitedblocks = []
        self.sizelimitedblocks = []

        for height in self.blockheights:
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            if block is None:
                continue
            inblocktxs = filter(lambda tx: tx.inblock, block.entries.values())
            if inblocktxs:
                block.avgtxsize = (
                    sum([tx.size for tx in inblocktxs]) / len(inblocktxs))
            else:
                block.avgtxsize = 0.
            if self.maxblocksize - block.size > block.avgtxsize:
                self.feelimitedblocks.append((block.height, block.size))
                txs.extend(tx_preprocess(block))
            else:
                self.sizelimitedblocks.append((block.height, block.size))

        if not txs and self.sizelimitedblocks:
            # All the blocks are close to the max block size.
            # This should happen rarely, so we just choose the smallest block.
            smallestheight = min(self.sizelimitedblocks, key=lambda x: x[1])[0]
            block = MemBlock.read(smallestheight, dbfile=dbfile)
            if block:
                txs.extend(tx_preprocess(block))

        if txs:
            self.stats = calc_stranding_feerate(txs)
            self.minfeerate = self.stats['sfr']
        else:
            logger.warning("Pool estimation: no valid transactions.")
            self.stats = {
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
            logger.warning("Pool estimation: only %d memblocks found out "
                           "of possible %d" % (numblocks, maxblocks))

    def _estimate_hashrate(self, blockrangetuple):
        retargets = [height for height in range(*blockrangetuple)
                     if not (height+1) % 2016]
        if not retargets or retargets[-1] != blockrangetuple[1] - 1:
            retargets += [blockrangetuple[1] - 1]

        pph = [get_pph(height) for height in retargets]
        numblocks = [0]*len(pph)

        idx = 0
        for height in sorted(self.blockheights):
            while height > retargets[idx]:
                idx += 1
            numblocks[idx] += 1

        ref_p = pph[0]
        totalblocks = 0.
        for k, p in zip(numblocks, pph):
            p_ratio = p / ref_p
            totalblocks += k / p_ratio

        T = (get_block_timestamp(blockrangetuple[1] - 1) -
             get_block_timestamp(blockrangetuple[0]))
        self.hashrate = totalblocks / ref_p / T


class PoolsEstimator(SimPools):
    def __init__(self):
        self.blockmap = {}
        self.pools = {}
        self.timestamp = 0.
        self.poolinfo = knownpools
        super(PoolsEstimator, self).__init__()

    def update(self):
        super(PoolsEstimator, self).update(self.pools)

    def start(self, blockrangetuple, stopflag=None, dbfile=history_file):
        logger.info("Beginning pool estimation "
                    "from blockrange({}, {})".format(*blockrangetuple))
        starttime = time()
        self.id_blocks(blockrangetuple, stopflag=stopflag)
        self.estimate_pools(stopflag=stopflag, dbfile=dbfile)
        self.calc_blockrate()
        self.update()
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
            except IndexError:
                raise IndexError("PoolEstimator: bad block range.")

            name = None
            for paddr, pattrs in self.poolinfo['payout_addresses'].items():
                candidate_name = pattrs['name']
                if paddr in baddrs:
                    if name is None:
                        name = candidate_name
                    elif name != candidate_name:
                        logger.warning(
                            "PoolsEstimator: "
                            "> 1 pools mapped to block %d" % height)

            for ptag, pattrs in self.poolinfo['coinbase_tags'].items():
                candidate_name = pattrs['name']
                if ptag in btag:
                    if name is None:
                        name = candidate_name
                    elif name != candidate_name:
                        logger.warning(
                            "PoolsEstimator: "
                            "> 1 pools mapped to block %d" % height)

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

            self.blockmap[height] = (name, blocksize)

        for height in self.blockmap.keys():
            if height < blockrangetuple[0] or height >= blockrangetuple[1]:
                del self.blockmap[height]

        if not self.blockmap:
            raise ValueError("Empty block range.")

        logger.info("Finished identifying blocks.")

    def estimate_pools(self, stopflag=None, dbfile=history_file):
        self.pools = {}
        blockrangetuple = (min(self.blockmap), max(self.blockmap)+1)

        def keyfunc(blocktuple):
            '''Select the pool name for itertools.groupby.'''
            return blocktuple[1][0]

        blockmap_items = sorted(self.blockmap.items(), key=keyfunc)
        for poolname, pool_blockmap_items in groupby(blockmap_items, keyfunc):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            blockheights = []
            blocksizes = []
            for b in pool_blockmap_items:
                blockheights.append(b[0])
                blocksizes.append(b[1][1])
            maxblocksize = max(blocksizes) if blocksizes else 0
            pool = PoolEstimate(blockheights, blockrangetuple, maxblocksize)
            pool.estimate_params(stopflag=stopflag, dbfile=dbfile)
            logger.info("Estimated %s: %s" % (poolname, repr(pool)))
            self.pools[poolname] = pool

    def calc_blockrate(self, currheight=None):
        if not currheight:
            currheight = max(self.blockmap)
        totalhashrate = sum([pool.hashrate for pool in self.pools.values()])
        curr_pph = get_pph(currheight)
        self.blockrate = curr_pph * totalhashrate

    def print_pools(self):
        poolitems = self._SimPools__pools
        table = Table()
        table.add_row(("Name", "Prop", "MBS", "MFR", "AKN", "BKN",
                       "mean", "std", "bias"))
        for name, pool in poolitems:
            table.add_row((
                name,
                '%.3f' % pool.proportion,
                pool.maxblocksize,
                pool.minfeerate,
                pool.stats['abovekn'],
                pool.stats['belowkn'],
                '%.2f' % pool.stats['mean'],
                '%.2f' % pool.stats['std'],
                '%.2f' % pool.stats['bias']))
        table.print_table()
        print("Avg block interval is %.2f" % (1./self.blockrate,))

    def get_stats(self):
        if not self:
            return None
        basestats = {
            'timestamp': self.timestamp,
            'blockinterval': 1/self.blockrate,
        }
        poolstats = {
            name: {
                'hashrate': pool.hashrate,
                'proportion': pool.proportion,
                'maxblocksize': pool.maxblocksize,
                'minfeerate': pool.minfeerate,
                'abovekn': pool.stats['abovekn'],
                'belowkn': pool.stats['belowkn'],
                'mean': pool.stats['mean'],
                'std': pool.stats['std'],
                'bias': pool.stats['bias']
            }
            for name, pool in self._SimPools__pools
        }
        basestats.update({'pools': poolstats})
        return basestats
