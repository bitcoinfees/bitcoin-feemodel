import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
from feemodel.measurement import TxRates, TxWaitTimes
from feemodel.simul import Simul
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint
from copy import deepcopy

defaultFeeValues = range(0, 100000, 10000)
blockRate = 1./600
tmpSaveFile = 'data/tmpsave.pickle'

pe = PoolEstimator(savePoolsFile=tmpSaveFile, minPoolBlocks=10)
pe.runEstimate((333931, 333953), dbFile=dbFile)
pprint(pe.getPools())
tr = TxRates(minRateTime=1)
tr.calcRates((333931, 333953), dbFile=dbFile)

class SimulTest(unittest.TestCase):
    def setUp(self):
        self.sim = Simul(pe, tr)

    def test_trans(self):
        # Test empty initial mempool
        waits, t, ni = self.sim.transient({})
        for feeRate, wait in waits:
            print(feeRate, wait)
        # Construct a mempool
        b = txmempool.Block.blockFromHistory(333931, dbFile=dbFile)
        waits, t, ni = self.sim.transient(b.entries)
        for feeRate, wait in waits:
            print(feeRate, wait)

    def test_ss(self):
        qstats, shorterrs, t, dum = self.sim.steadyState(miniters=1, maxiters=10000)
        pprint(qstats)
        print("short errs:")
        pprint(shorterrs)

    def tearDown(self):
        if os.path.exists(tmpSaveFile):
            os.remove(tmpSaveFile)

class UnstableTest(unittest.TestCase):
    def test_unstable(self):
        self.tr = deepcopy(tr)
        self.tr.txRate = 1000000
        sim = Simul(pe, self.tr)
        self.assertRaises(ValueError, sim.steadyState)

if __name__ == '__main__':
    unittest.main()

