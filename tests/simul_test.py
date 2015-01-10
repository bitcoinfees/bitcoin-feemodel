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
        waits, t = self.sim.transient({})
        for feeRate, wait in waits:
            print(feeRate, wait)
        # Construct a mempool
        b = txmempool.Block.blockFromHistory(333931, dbFile=dbFile)
        waits, t = self.sim.transient(b.entries)
        for feeRate, wait in waits:
            print(feeRate, wait)

    def test_ss(self):
        qstats, t, dum = self.sim.steadyState(miniters=1, maxiters=1000)
        pprint(qstats)

    def tearDown(self):
        if os.path.exists(tmpSaveFile):
            os.remove(tmpSaveFile)

if __name__ == '__main__':
    unittest.main()

