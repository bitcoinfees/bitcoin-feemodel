import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
from feemodel.measurement import TxRates
from feemodel.simul import Simul
import feemodel.pools
from testconfig import dbFile
from operator import add
from random import expovariate
from math import log
from pprint import pprint
from copy import deepcopy
from bisect import bisect

savePoolsFile = 'data/savePools.pickle'
blockRate = 1./600

pe = PoolEstimator(savePoolsFile=savePoolsFile, minPoolBlocks=1)
pe.identifyPoolBlocks((333931, 333953))
pe.estimatePools(dbFile=dbFile)
tr = TxRates(minRateTime=1)
tr.calcRates((333931, 333953), dbFile=dbFile)

class PoolEstimatorTests(unittest.TestCase):
    def test_poolIO(self):
        self.assertEqual(pe.poolsCache, pe.pools)
        pe.saveObject()
        pe2 = PoolEstimator.loadObject(savePoolsFile)
        self.assertEqual(pe,pe2)
        print(pe)
        pprint(pe.getPools(), width=300)

        os.remove(savePoolsFile)

    def test_poolClears(self):
        self.pe = PoolEstimator(poolBlocksWindow=5, savePoolsFile=savePoolsFile)
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
        self.pe = deepcopy(pe)

    def test_processingConverges(self):
        '''Crude convergence test. This is probabilistic but we just want to make sure
        selectRandomPool is working sanely.'''
        mfrs, pr, upr = self.pe.getProcessingRate(blockRate)
        sampleProcessingRate = [ProcessingRate(mfr) for mfr in mfrs]
        totaltime = 0.
        for i in xrange(500000):
            totaltime += expovariate(blockRate)
            poolName, maxBlockSize, minFeeRate = self.pe.selectRandomPool()
            for feeRate in sampleProcessingRate:
                feeRate.nextBlock(maxBlockSize, minFeeRate)

        rates = [feeRate.calcAvgRate(totaltime) for feeRate in sampleProcessingRate]
        ratesDiff = [abs(log(rates[idx]) - log(pr[idx])) for idx in range(len(mfrs))]
        print("max ratesDiff: %.4f" % max(ratesDiff))
        pprint([(mfrs[idx], rates[idx], pr[idx]) for idx in range(len(mfrs))])
        self.assertTrue(max(ratesDiff) < 0.1)

    def test_insufficientPools(self):
        self.pe.minPoolBlocks = 2016
        self.assertRaises(ValueError, self.pe.selectRandomPool)
        self.assertRaises(ValueError, self.pe.getProcessingRate, 1./600)


class CapacityTest(unittest.TestCase):
    def test_cap(self):
        pe = PoolEstimator.loadObject(savePoolsFile = 'data/testpools.pickle')
        ac, er, pc = pe.calcCapacities(tr, blockRate)
        pprint(ac)
        feeRates = [feeRate for feeRate, cap in ac]
        simProc = {name: [[feeRate, 0.] for feeRate in feeRates] for name in pc.keys()}
        sim = Simul(pe, tr, blockRate)
        sim.initCalcs()
        sim.initMempool({})
        print("Stable fee rate is %d" % sim.stableFeeRate)
        totalTime = 0.
        info = {'poolName': None}
        for i in range(1000):
            if not i % 100:
                print(i)
            totalTime += sim.addToMempool()
            preTxList = sim.txNoDeps[:]
            sim.processBlock(info=info)
            postTxList = sim.txNoDeps[:]

            for tx in postTxList:
                preTxList.remove(tx)

            for tx in preTxList:
                fidx = bisect(feeRates, tx.feeRate)
                if fidx != 0:
                    simProc[info['poolName']][fidx-1][1] += tx.size

        for name, cap in simProc.items():
            for c in cap:
                c[1] /= totalTime
            if pc[name].proportion > 0.01:
                print("\nPool %s:" % name)
                print("Feerate\tsim\tcalc\tcap")
                print("====================")
                for csim, c in zip(cap, sorted(pc[name].capacities.items())):
                    print("%d\t%.2f\t%.2f\t%.2f" % (csim[0], csim[1], c[1][0], c[1][1]))


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
