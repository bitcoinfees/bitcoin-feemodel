import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from testconfig import dbFile, tmpdbFile
import threading
from random import choice
from feemodel.stranding import txPreprocess, calcStrandingFeeRate

nonparam.numBlocksUsed = (6,15)

# Might need to change these - given that blocktime has been changed to int
class PushBlockTests(unittest.TestCase):
    def setUp(self):
        self.testBlockHeight = 333931
        self.block = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=dbFile)
        self.np = nonparam.NonParam(bootstrap=False)

    def test_regular(self):
        self.np.pushBlocks([self.block])      
        feeEstimate = self.np.blockEstimates[self.testBlockHeight].feeEstimate
        self.assertEquals(feeEstimate.minFeeRate, 23310)
        self.assertEquals(feeEstimate.abovekn, (490,493))
        self.assertEquals(feeEstimate.belowkn, (282,285))
        
        txs = txPreprocess(self.block)
        est = calcStrandingFeeRate(txs)
        print("regular", est)
        self.assertEquals(est['sfr'], 23310)

    def test_empty(self):
        self.block.entries = {}
        self.np.pushBlocks([self.block])       
        self.assertFalse(self.np.blockEstimates)

        self.assertRaises(ValueError, calcStrandingFeeRate, [])

    def test_allInBlock(self):
        self.block.entries = {txid: entry for txid,entry in self.block.entries.iteritems()
            if entry['inBlock']}
        self.np.pushBlocks([self.block])      
        self.assertEquals(self.np.blockEstimates[self.testBlockHeight].feeEstimate.minFeeRate, 13940)

        txs = txPreprocess(self.block)
        est = calcStrandingFeeRate(txs)
        print('allinblock', est)
        self.assertEquals(est['sfr'], 13940)

    def test_zeroInBlock(self):
        self.block.entries = {txid: entry for txid,entry in self.block.entries.iteritems()
            if not entry['inBlock']}
        self.np.pushBlocks([self.block])       
        self.assertFalse(self.np.blockEstimates)
        self.assertTrue(self.np.zeroInBlock)
        blocks = [txmempool.Block.blockFromHistory(height, dbFile=dbFile)
            for height in range(333932,333941)]
        self.np.pushBlocks(blocks)        
        self.assertFalse(self.np.zeroInBlock)
        mlts = [est.minLeadTime for height, est in self.np.blockEstimates.items() 
            if height != self.testBlockHeight]
        p90 = mlts[9*len(mlts)//10 - 1]
        blockEstimate = self.np.blockEstimates[self.testBlockHeight]
        self.assertEquals(blockEstimate.minLeadTime, p90)
        self.assertEquals(blockEstimate.feeEstimate.minFeeRate, float("inf"))

        txs = txPreprocess(self.block)
        est = calcStrandingFeeRate(txs)
        print('zeroinblock', est)
        self.assertEquals(est['sfr'], float('inf'))

    def test_removeStats(self):
        blocks = [txmempool.Block.blockFromHistory(height, dbFile=dbFile)
            for height in range(333931,333953)]
        self.np.pushBlocks(blocks)     
        self.assertFalse(self.np.zeroInBlock)
        self.assertEquals(len(self.np.blockEstimates),nonparam.numBlocksUsed[1])

    def test_concurrentRaises(self):
        self.np = nonparam.NonParam()
        secondBlock = txmempool.Block.blockFromHistory(self.testBlockHeight+1, dbFile=dbFile)
        t = threading.Thread(target=self.np.pushBlocks, args=([self.block],))
        t.start()
        self.assertRaises(AssertionError, self.np.pushBlocks, [secondBlock])
        t.join()


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.blocks = [txmempool.Block.blockFromHistory(height, dbFile=dbFile)
            for height in range(333931,333953)]
        self.np = nonparam.NonParam(bootstrap=False)
        self.np.pushBlocks(self.blocks)

    def test_estimateFee(self):
        self.assertRaises(ValueError, self.np.estimateFee, 0)
        self.assertRaises(ValueError, self.np.estimateFee, 1e6)
        for nBlocks in range(1, 7):
            print(nBlocks, self.np.estimateFee(nBlocks))

    def test_estimateTx(self):
        for i in range(100):
            entry = None
            while not entry:
                try:
                    block = choice(self.blocks)
                    entry = choice(block.entries.values())
                except:
                    pass
            try:
                nBlocks = self.np.estimateTx(entry)
                feeEstimate = self.np.estimateFee(nBlocks)
                print(entry['feeRate'], feeEstimate, nBlocks)
            except (ModelError, ValueError) as e:
                print(e)


if __name__ == '__main__':
    unittest.main()



