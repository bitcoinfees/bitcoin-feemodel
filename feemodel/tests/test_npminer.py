from __future__ import division

import unittest
from collections import defaultdict
from math import log

from feemodel.util import cumsum_gen
from feemodel.simul.pools import SimPoolsNP


BLOCKRATE = 1 / 600
MAXBLOCKSIZES = [1000000, 750000, 750000, 950000]
MINFEERATES = [10000, 1000, 1000]


class NPMinerTests(unittest.TestCase):

    def setUp(self):
        self.simpools = SimPoolsNP(MAXBLOCKSIZES, MINFEERATES,
                                   blockrate=BLOCKRATE)

    def test_A(self):
        """Test gen convergence."""
        size_accum = defaultdict(int)
        interval_accum = 0
        for idx, (simblock, blockinterval) in enumerate(
                self.simpools.blockgen()):
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
        cap_fn = self.simpools.get_capacityfn()
        print(cap_fn)
        for feerate, cap in zip(feerates, cumcaps):
            print(feerate, cap)
            ref = cap_fn(feerate)
            logdiff = abs(log(ref) - log(cap))
            self.assertLess(logdiff, 0.01)


if __name__ == '__main__':
    unittest.main()
