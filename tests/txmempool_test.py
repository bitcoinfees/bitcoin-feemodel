import unittest
import os
from copy import deepcopy
import feemodel.txmempool as txmempool
from feemodel.util import getHistory
from testconfig import dbFile, tmpdbFile
txmempool.keepHistory = 10

class BlockTests(unittest.TestCase):
    def setUp(self):
        self.testBlockHeight = 333931
        self.block = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=dbFile)

    def test_writeread(self):
        self.block.writeHistory(dbFile=tmpdbFile)
        blockread = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=tmpdbFile)
        self.assertEqual(blockread,self.block)

    def test_writereadempty(self):
        self.block.entries = {}
        self.block.writeHistory(dbFile=tmpdbFile)
        blockread = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=tmpdbFile)
        self.assertEqual(blockread,self.block)


    def test_deletehistory(self):
        blocks = [txmempool.Block.blockFromHistory(height, dbFile=dbFile)
            for height in range(333931,333953)]

        for block in blocks:
            if block:
                block.writeHistory(dbFile=tmpdbFile)

        blocksHistory = getHistory(tmpdbFile)
        self.assertEqual(len(blocksHistory), txmempool.keepHistory)

    def tearDown(self):
        if os.path.exists(tmpdbFile):
            os.remove(tmpdbFile)        


class TxMempoolTests(unittest.TestCase):
    def setUp(self):
        self.testBlockHeight = 333931
        self.block = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=dbFile)
        self.blockHeightRange = range(self.testBlockHeight, self.testBlockHeight+1)

        entries = deepcopy(self.block.entries)
        for entry in entries.values():
            del entry['leadTime'], entry['feeRate'], entry['inBlock']

        self.currPool = entries

    def test_processBlocks(self):
        processedBlock = txmempool.TxMempool.processBlocks(self.blockHeightRange,
            self.currPool, deepcopy(self.currPool), self.block.time)[0]
        self.assertEqual(processedBlock, self.block)

    def test_processEmptyMempool(self):
        self.block.entries = {}
        processedBlock = txmempool.TxMempool.processBlocks(self.blockHeightRange,
            {},{},self.block.time)[0]
        self.assertEqual(processedBlock, self.block)



if __name__ == '__main__':
    unittest.main()