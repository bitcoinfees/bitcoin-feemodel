import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint

savePoolsFile = 'data/savePools.pickle'
testPoolsFile = 'data/testPools.pickle'
blockRate = 1./600

feemodel.pools.minPoolBlocks = 1
pe = PoolEstimator(savePoolsFile=savePoolsFile)
pe.identifyPoolBlocks((333931, 333953))
pe.estimatePools(dbFile=dbFile)

class PoolEstimatorTests(unittest.TestCase):
    def setUp(self):
        feemodel.pools.minPoolBlocks = 1

    def test_poolIO(self):
        self.assertEqual(pe.poolsCache, pe.pools)
        pe.saveObject()
        pe2 = PoolEstimator.loadObject(savePoolsFile)
        self.assertEqual(pe,pe2)
        print(pe)

        os.remove(savePoolsFile)

    def test_poolClears(self):
        self.pe = PoolEstimator(5, savePoolsFile)
        self.pe.identifyPoolBlocks((333931,333953))
        self.pe.estimatePools(dbFile=dbFile)
        heights = reduce(add, [list(pool.blockHeights) for pool in self.pe.poolsCache.values()], [])
        self.assertEqual(len(heights), 5)
        self.assertEqual(self.pe.poolsCache, self.pe.pools)
        self.pe.saveObject()
        pe2 = PoolEstimator.loadObject(savePoolsFile)
        self.assertEqual(self.pe,pe2)
        print(self.pe)

        os.remove(savePoolsFile)



class RandomPoolTest(unittest.TestCase):
    def setUp(self):
        #self.pe = PoolEstimator.loadObject(testPoolsFile)
        self.pe = pe
        feemodel.pools.minPoolBlocks = 1

    def test_processingConverges(self):
        '''Crude convergence test. This is probabilistic but we just want to make sure
        selectRandomPool is working sanely.'''
        mfrs, pr, upr = self.pe.getProcessingRate(blockRate)
        sampleProcessingRate = [ProcessingRate(mfr) for mfr in mfrs]
        totaltime = 0.
        for i in xrange(500000):
            totaltime += expovariate(blockRate)
            maxBlockSize, minFeeRate = self.pe.selectRandomPool()
            for feeRate in sampleProcessingRate:
                feeRate.nextBlock(maxBlockSize, minFeeRate)
                
        rates = [feeRate.calcAvgRate(totaltime) for feeRate in sampleProcessingRate]
        ratesDiff = [abs(log(rates[idx]) - log(pr[idx])) for idx in range(len(mfrs))]
        print("max ratesDiff: %.4f" % max(ratesDiff))
        pprint([(mfrs[idx], rates[idx], pr[idx]) for idx in range(len(mfrs))])
        self.assertTrue(max(ratesDiff) < 0.1)

    def test_insufficientPools(self):
        feemodel.pools.minPoolBlocks = 2016
        self.assertRaises(ValueError, self.pe.selectRandomPool)
        self.assertRaises(ValueError, self.pe.getProcessingRate, 1./600)


class ProcessingRate(object):
    def __init__(self, feeRate):
        self.feeRate = feeRate
        self.totalBytes = 0

    def nextBlock(self, maxBlockSize, minFeeRate):
        if minFeeRate <= self.feeRate:
            self.totalBytes += maxBlockSize

    def calcAvgRate(self, totaltime):
        return self.totalBytes / totaltime

if __name__ == '__main__':
    unittest.main()
