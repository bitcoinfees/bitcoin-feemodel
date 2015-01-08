import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
from feemodel.measurement import TxRates, TxWaitTimes
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint

defaultFeeValues = range(0, 100000, 10000)
testRatesFile = 'data/testRates.pickle'
tmpSaveFile = 'data/tmpsave.pickle'
blockRate = 1./600

class TxSampleTest(unittest.TestCase):
    def setUp(self):
        self.tr = TxRates.loadObject(testRatesFile)
        self.feeClassValues = range(0, 100000, 5000)

    def test_txSampleConverges(self):
        byteRates, txRate = self.tr.getByteRate((334754,334774), self.feeClassValues)
        sampleByteRates = [TxByteRate(feeClass) for feeClass in self.feeClassValues]
        totaltime = 0.
        for i in xrange(1000):
            t = expovariate(blockRate)
            txSample = self.tr.generateTxSample(t*txRate)
            totaltime += t
            for feeClass in sampleByteRates:
                feeClass.nextTxs(txSample)

        rates = [feeClass.calcAvgRate(totaltime) for feeClass in sampleByteRates]
        ratesDiff = [abs(log(rates[idx]) - log(byteRates[idx])) for idx in range(len(byteRates))]
        print("max ratesDiff: %.4f" % max(ratesDiff))
        pprint([(self.feeClassValues[idx], rates[idx], byteRates[idx]) for idx in range(len(self.feeClassValues))])
        self.assertTrue(max(ratesDiff) < 0.1)

class TxRatesTest(unittest.TestCase):
    def test_rates(self):
        tr = TxRates(samplingWindow=3, txRateWindow=2016, saveRatesFile=tmpSaveFile)
        tr2 = TxRates(samplingWindow=3, txRateWindow=2016, saveRatesFile=tmpSaveFile)
        lh = txmempool.LoadHistory(dbFile)
        lh.registerFn(tr.pushBlocks, (333949, 333953))
        lh.registerFn(tr2.pushBlocks, (333931, 333953))
        lh.loadBlocks()
        print(len(tr.txSamplesCache))
        self.assertEqual(tr.txSamplesCache, tr2.txSamplesCache)
        print(tr.calcRates((333931, 333953)))
        print(tr2.calcRates((333931, 333953)))

        tr.saveObject()
        tr2 = TxRates.loadObject(tmpSaveFile)

        self.assertEqual(tr, tr2)
        os.remove(tmpSaveFile)


class WaitTest(unittest.TestCase):
    def test_wait(self):
        wt = TxWaitTimes(defaultFeeValues, 2016, tmpSaveFile)
        lh = txmempool.LoadHistory(dbFile)
        lh.registerFn(wt.pushBlocks, (333931,333953))
        lh.loadBlocks()
        pprint(wt.waitTimesCache)
        wt.saveObject()
        wt2 = TxWaitTimes.loadObject(tmpSaveFile)
        self.assertEqual(wt, wt2)
        os.remove(tmpSaveFile)

class TxByteRate(object):
    def __init__(self, feeClass):
        self.feeClass = feeClass
        self.totalBytes = 0

    def nextTxs(self, txList):
        for tx in txList:
            if tx.feeRate >= self.feeClass:
                self.totalBytes += tx.size

    def calcAvgRate(self, totaltime):
        return self.totalBytes / totaltime

if __name__ == '__main__':
    unittest.main()
