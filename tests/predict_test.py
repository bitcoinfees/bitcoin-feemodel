import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
from feemodel.measurement import TxRates, TxWaitTimes
from feemodel.simul import Simul, Predictions, TransientWait, TransientStats
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint
import pickle
from copy import deepcopy

defaultFeeValues = range(0, 100000, 10000)
tmpSaveFile = 'data/tmpsave.pickle'


class PredictTest(unittest.TestCase):
    def setUp(self):
        self.waitTimes = [(feeRate, TransientWait()) for feeRate in defaultFeeValues]
        self.tStats = TransientStats()
        self.predictions = Predictions(self.tStats, defaultFeeValues, 2016)
        self.b = txmempool.Block.blockFromHistory(333931, dbFile=dbFile)
        self.mapTx = deepcopy(self.b.entries)
        for entry in self.mapTx.values():
            del entry['feeRate']

    def test_infInterval(self):
        for feeRate, tw in self.waitTimes:
            tw.predictionInterval = 100000
        self.tStats.update(self.waitTimes, 0, None)
        self.predictions.updatePredictions(self.mapTx)
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
        self.tStats.update(self.waitTimes, 0, None)
        self.predictions.updatePredictions(self.mapTx)
        self.predictions.pushBlocks([self.b])
        score = self.predictions.getScore()
        totalPredicts = sum([p[1] for f,p in score.items()])
        self.assertTrue(totalPredicts > 0)
        print("Total predicts is %d" % totalPredicts)
        self.assertTrue(all([p[0] == 0 for p in score.values()]))
        pprint(score)

    def test_points(self):
        with open('data/testtrans.pickle', 'rb') as f:
            self.waitTimes = pickle.load(f)
        self.tStats.update(self.waitTimes, 0, None)
        print("Prediction values\n\nfeerate\tavg\tpred\tpredinterp\n=========================\n")
        for feeRate, w in self.waitTimes:
            print("%d\t%.2f\t%.2f\t%.2f" % (feeRate,
                                      w.mean,
                                      w.predictionInterval,
                                      self.tStats.predictConf({'feeRate': feeRate+10, 'time': 0.})))
        self.assertEqual(self.tStats.predictConf({'feeRate': 0, 'time': 0.}), None)
        self.assertEqual(self.tStats.predictConf({'feeRate': 1000000, 'time': 0.}), self.waitTimes[-1][1].predictionInterval)

        # Inverse predictconf
        print("Inverse predict\n\nconftime\tpred\n===============\n")
        for conftime in [0, 600, 700., 1000, 1200, 2000, 5000]:
            feeRate = self.tStats.inverseAvgConf(conftime)
            if feeRate is None:
                feeRate = float("inf")
            print("%d\t%.2f" % (conftime, feeRate))

        print(self.tStats.ay)





if __name__ == '__main__':
    unittest.main()

