import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
from feemodel.measurement import TxRates, TxWaitTimes
from feemodel.simul import Simul, Predictions, WaitStats, TransientStats
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint

defaultFeeValues = range(0, 100000, 10000)
tmpSaveFile = 'data/tmpsave.pickle'


class PredictTest(unittest.TestCase):
    def setUp(self):
        self.waitTimes = [(feeRate, WaitStats()) for feeRate in defaultFeeValues]
        self.tStats = TransientStats()
        self.tStats.update(self.waitTimes, 0, None)
        self.predictions = Predictions(self.tStats, defaultFeeValues, 2016)
        self.b = txmempool.Block.blockFromHistory(333931, dbFile=dbFile)

    def test_infInterval(self):
        for feeRate, tw in self.waitTimes:
            tw.predictionInterval = float("inf")
        mapTx = self.b.entries
        self.predictions.updatePredictions(mapTx)
        self.predictions.pushBlocks([self.b])
        score = self.predictions.getScore()
        totalPredicts = sum([p[1] for f,p in score.items()])
        self.assertTrue(totalPredicts > 0)
        print("Total predicts is %d" % totalPredicts)
        self.assertTrue(all([p[0] == p[1] for p in score.values()]))
        pprint(score)

    def test_zeroInterval(self):
        for feeRate, tw in self.waitTimes:
            tw.predictionInterval = 0.
        mapTx = self.b.entries
        self.predictions.updatePredictions(mapTx)
        self.predictions.pushBlocks([self.b])
        score = self.predictions.getScore()
        totalPredicts = sum([p[1] for f,p in score.items()])
        self.assertTrue(totalPredicts > 0)
        print("Total predicts is %d" % totalPredicts)
        self.assertTrue(all([p[0] == 0 for p in score.values()]))
        pprint(score)



if __name__ == '__main__':
    unittest.main()

