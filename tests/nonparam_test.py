import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.util import logWrite
from testconfig import dbFile, tmpdbFile

nonparam.numBlocksUsed = (6,15)

class PushBlockTests(unittest.TestCase):
    def setUp(self):
        self.testBlockHeight = 333931
        self.block = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=dbFile)
        self.model = nonparam.NonParam()

    def test_regular(self):
        self.model.pushBlocks([self.block])      
        feeEstimate = self.model.blockEstimates[self.testBlockHeight].feeEstimate
        self.assertEquals(feeEstimate.minFeeRate, 23310)
        self.assertEquals(feeEstimate.abovekn, (489,492))
        self.assertEquals(feeEstimate.belowkn, (281,284))

    def test_empty(self):
        self.block.entries = {}
        self.model.pushBlocks([self.block])       
        self.assertFalse(self.model.blockEstimates)

    def test_allInBlock(self):
        self.block.entries = {txid: entry for txid,entry in self.block.entries.iteritems()
            if entry['inBlock']}
        self.model.pushBlocks([self.block])      
        self.assertEquals(self.model.blockEstimates[self.testBlockHeight].feeEstimate.minFeeRate, 13940)

    def test_zeroInBlock(self):
        self.block.entries = {txid: entry for txid,entry in self.block.entries.iteritems()
            if not entry['inBlock']}
        self.model.pushBlocks([self.block])       
        self.assertFalse(self.model.blockEstimates)
        self.assertTrue(self.model.zeroInBlock)
        blocks = [txmempool.Block.blockFromHistory(height, dbFile=dbFile)
            for height in range(333932,333941)]
        self.model.pushBlocks(blocks)        
        self.assertFalse(self.model.zeroInBlock)
        mlts = [est.minLeadTime for height, est in self.model.blockEstimates.items() 
            if height != self.testBlockHeight]
        p90 = mlts[9*len(mlts)//10 - 1]
        blockEstimate = self.model.blockEstimates[self.testBlockHeight]
        self.assertEquals(blockEstimate.minLeadTime, p90)
        self.assertEquals(blockEstimate.feeEstimate.minFeeRate, float("inf"))

    def test_removeStats(self):
        blocks = [txmempool.Block.blockFromHistory(height, dbFile=dbFile)
            for height in range(333931,333953)]
        self.model.pushBlocks(blocks)     
        self.assertFalse(self.model.zeroInBlock)
        self.assertEquals(len(self.model.blockEstimates),nonparam.numBlocksUsed[1])




if __name__ == '__main__':
    unittest.main()



