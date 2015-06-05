from __future__ import division

import unittest
from operator import attrgetter
from collections import defaultdict
from copy import deepcopy

from feemodel.tests.config import poolsref, txref
from feemodel.simul.simul import Simul, UTILIZATION_THRESH
from feemodel.util import cumsum_gen


def checkcaps(sim):
    NUMITERS = 100000
    bytecounter = defaultdict(int)
    stable_byterate = sim.cap.txbyteratefn(sim.stablefeerate)
    print("The stable feerate is {}.".format(sim.stablefeerate))
    print("The stable/total byterate is {}/{}.".
          format(stable_byterate, sim.cap.txbyteratefn(0)))

    for idx, simblock in enumerate(sim.run()):
        bytecounter[simblock.pool.minfeerate] += simblock.size
        if not idx % 10000:
            print(idx)
        if idx == NUMITERS:
            break

    mempool_entries = sim.mempool.get_entries()
    mempool_size = sum(map(attrgetter("size"), mempool_entries.values()))
    print("final mempool len/size: {}/{}".
          format(len(mempool_entries), mempool_size))

    for feerate in bytecounter:
        bytecounter[feerate] /= sim.simtime

    feerates, procrates = zip(*sorted(bytecounter.items()))
    cumprocrates = list(cumsum_gen(procrates))
    try:
        loweststablemfr = sim.cap.procratesfn._x[0]
    except IndexError:
        loweststablemfr = float("inf")

    for feerate, cumprocrate in zip(feerates, cumprocrates):
        if feerate < loweststablemfr:
            continue
        lowercumprocrate = sum(
            [procrate for _feerate, procrate
             in sim.cap.procratesfn if _feerate > feerate])
        upper_byterate = stable_byterate - lowercumprocrate
        caprate = sim.cap.capfn(feerate)
        print("feerate/actual byterate/upperbound byterate/cap: {}/{}/{}/{}".
              format(feerate, cumprocrate, upper_byterate, caprate))
        assert cumprocrate < upper_byterate*1.01
        assert upper_byterate < caprate*UTILIZATION_THRESH


class CapacityTests(unittest.TestCase):

    def setUp(self):
        self.poolsref = deepcopy(poolsref)
        self.txref = deepcopy(txref)

    def test_A(self):
        """Default ref tests."""
        sim = Simul(self.poolsref, self.txref)
        lowestmfr = min(
            map(attrgetter("minfeerate"), poolsref.pools.values()))
        self.assertEqual(sim.stablefeerate, lowestmfr)
        self.assertEqual(sum(sim.cap.procratesfn._y),
                         sim.cap.txbyteratefn(sim.stablefeerate))

        print("Starting Test A")
        print("===============")
        checkcaps(sim)

    def test_B(self):
        """Increasing the txrate."""
        self.txref.txrate = 1.8
        sim = Simul(self.poolsref, self.txref)

        print("Starting Test B")
        print("===============")
        checkcaps(sim)


if __name__ == '__main__':
    unittest.main()
