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
tmpSaveFile = 'data/tmpsave'
blockRate = 1./600

class TxSampleTest(unittest.TestCase):
    def test_txSampleConverges(self):
        tr = TxRates(minRateTime=1)
        tr.calcRates((333931, 333953), dbFile=dbFile)
        byteRates, txRate = tr.getByteRate(defaultFeeValues)
        sampleByteRates = [TxByteRate(feeClass) for feeClass in defaultFeeValues]
        totaltime = 0.
        for i in xrange(1000):
            t = expovariate(blockRate)
            txSample = tr.generateTxSample(t*txRate)
            totaltime += t
            for feeClass in sampleByteRates:
                feeClass.nextTxs(txSample)

        rates = [feeClass.calcAvgRate(totaltime) for feeClass in sampleByteRates]
        ratesDiff = [abs(log(rates[idx]) - log(byteRates[idx])) for idx in range(len(byteRates))]
        print("max ratesDiff: %.4f" % max(ratesDiff))
        pprint([(defaultFeeValues[idx], rates[idx], byteRates[idx]) for idx in range(len(defaultFeeValues))])
        self.assertTrue(max(ratesDiff) < 0.1)

class TxRatesTest(unittest.TestCase):
    def test_rates(self):
        tr = TxRates(minRateTime=1, saveRatesFile=tmpSaveFile)
        tr.calcRates((333931, 333953), dbFile=dbFile)
        byteRates, txRate = tr.getByteRate(defaultFeeValues)
        for idx, feeRate in enumerate(defaultFeeValues):
            print(feeRate, byteRates[idx])
        print("txRate is %.2f" % txRate)
        print("num samples is %d" % len(tr.txSamples))
        print("total time is %.2f" % tr.totalTime)
        print("num unique samples: %d" % len(set([s.txid for s in tr.txSamples])))
        tr.saveObject()
        tr2 = TxRates.loadObject(tmpSaveFile)
        self.assertEqual(tr, tr2)

    def test_minRateTime(self):
        tr = TxRates(minRateTime=10000)
        self.assertRaises(ValueError, tr.calcRates, (333931, 333953), dbFile=dbFile)

    def test_maxSamples(self):
        tr = TxRates(minRateTime=1, maxSamples=4000)
        tr.calcRates((333931, 333953), dbFile=dbFile)
        byteRates, txRate = tr.getByteRate(defaultFeeValues)
        self.assertEqual(len(tr.txSamples), 4000)
        for idx, feeRate in enumerate(defaultFeeValues):
            print(feeRate, byteRates[idx])
        print("txRate is %.2f" % txRate)
        print("num samples is %d" % len(tr.txSamples))
        print("total time is %.2f" % tr.totalTime)
        print("total txs is %d" % tr.totalTxs)
        print("num unique samples: %d" % len(set([s.txid for s in tr.txSamples])))

    def test_calcRates(self):
        tr = TxRates(minRateTime=1)
        tr.calcRates((333931, 333953), dbFile=dbFile)
        numiters = 100
        btime = 0.
        tr2 = TxRates(minRateTime=1, maxSamples=10000)
        prevBlock = None
        for i in range(numiters):
            t = expovariate(blockRate)
            btime += t
            txs = tr.generateTxSample(t*tr.txRate)
            b = txmempool.Block(
                {'%d_%d' % (idx, i): {'size': tx.size, 'feeRate': tx.feeRate}
                    for idx, tx in enumerate(txs)},
                i,
                999999,
                btime
            )
            tr2.addBlock(b, prevBlock)
            prevBlock = b
        tr2.txRate = tr2.totalTxs / tr2.totalTime
        byteRates, txRate = tr.getByteRate(defaultFeeValues)
        byteRates2, txRate2 = tr2.getByteRate(defaultFeeValues)
        print("Testing calcrates...")
        pprint([(byteRates[idx], byteRates2[idx]) for idx in range(len(byteRates))])
        ratesDiff = [abs(log(byteRates[idx]) - log(byteRates2[idx])) for idx in range(len(byteRates))]
        print("max ratesDiff: %.4f" % max(ratesDiff))
        self.assertTrue(max(ratesDiff) < 0.5)

    def tearDown(self):
        if os.path.exists(tmpSaveFile):
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
