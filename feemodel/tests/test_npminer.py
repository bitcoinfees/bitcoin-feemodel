from __future__ import division

import unittest
from collections import defaultdict
from math import log
from time import sleep

from feemodel.tests.config import tmpdatadir_context
from feemodel.tests.pseudoproxy import install
from feemodel.util import cumsum_gen
from feemodel.simul.pools import SimPoolsNP
from feemodel.txmempool import MemBlock

from feemodel.estimate.pools import PoolsEstimatorNP
from feemodel.app.pools import PoolsOnlineEstimator

install()

BLOCKRATE = 1 / 600
MAXBLOCKSIZES = [1000000, 750000, 750000, 950000]
MINFEERATES = [10000, 1000, 1000]


class NPMinerTests(unittest.TestCase):

    def test_A(self):
        """Test gen convergence."""
        simpools = SimPoolsNP(MAXBLOCKSIZES, MINFEERATES,
                              blockrate=BLOCKRATE)
        size_accum = defaultdict(int)
        interval_accum = 0
        for idx, (simblock, blockinterval) in enumerate(
                simpools.blockgen()):
            if idx > 100000:
                break
            size_accum[simblock.pool.minfeerate] += simblock.pool.maxblocksize
            interval_accum += blockinterval

        interval_samplemean = interval_accum / idx
        logdiff = abs(log(1 / BLOCKRATE) - log(interval_samplemean))
        self.assertLess(logdiff, 0.01)

        feerates, caps = zip(*sorted([
            (feerate, totalsize / interval_accum)
            for feerate, totalsize in size_accum.items()
        ]))
        cumcaps = list(cumsum_gen(caps))
        cap_fn = simpools.get_capacityfn()
        hashrate_fn = simpools.get_hashratefn()
        print(cap_fn)
        print(hashrate_fn)
        for feerate, cap in zip(feerates, cumcaps):
            print(feerate, cap)
            ref = cap_fn(feerate)
            logdiff = abs(log(ref) - log(cap))
            self.assertLess(logdiff, 0.01)

    def test_B(self):
        """Test estimation."""
        with tmpdatadir_context():
            pe = PoolsEstimatorNP()
            pe.start((333931, 333954))

            # A fake memblock with zero entries.
            empty_memblock = MemBlock()
            empty_memblock.blockheight = 333954
            empty_memblock.height = 333953
            empty_memblock.blocksize = 0
            empty_memblock.time = pe.blockstats[333953][2] + 20
            empty_memblock.entries = {}
            pe.update(empty_memblock)
        print(pe)

    def test_C(self):
        """Test app."""
        with tmpdatadir_context():
            peo = PoolsOnlineEstimator(333954, None, 2016)
            sleep(1)
            peo.loadingthread.join()
            self.assertIsNotNone(peo.poolsestimate)
            self.assertFalse(peo.poolsestimate)

            peo = PoolsOnlineEstimator(333954, None, 2016, minblocks=1)
            sleep(1)
            peo.loadingthread.join()
            self.assertIsNotNone(peo.poolsestimate)
            self.assertTrue(peo.poolsestimate)
            print(peo.poolsestimate)


if __name__ == '__main__':
    unittest.main()
