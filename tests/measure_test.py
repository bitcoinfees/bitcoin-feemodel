import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
from feemodel.measurement import TxRates
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint

testRatesFile = 'data/testRates.pickle'
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
