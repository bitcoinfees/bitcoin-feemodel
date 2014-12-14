import unittest
import os
from copy import deepcopy
import feemodel.txmempool as txmempool
from feemodel.util import DummyModel
from testconfig import dbFile, tmpdbFile

class BlockTests(unittest.TestCase):
    def setUp(self):
        self.testBlockHeight = 333931
        self.block = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=dbFile)

    def test_writeread(self):
        try:
            self.block.writeHistory(dbFile=tmpdbFile)
            blockread = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=tmpdbFile)
            self.assertEqual(blockread,self.block)
        finally:
            if os.path.exists(tmpdbFile):
                os.remove(tmpdbFile)

    def test_writereadempty(self):
        self.block.entries = {}
        try:
            self.block.writeHistory(dbFile=tmpdbFile)
            blockread = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=tmpdbFile)
            self.assertEqual(blockread,self.block)
        finally:
            if os.path.exists(tmpdbFile):
                os.remove(tmpdbFile)


class TxMempoolTests(unittest.TestCase):
    def setUp(self):
        self.testBlockHeight = 333931
        self.model = DummyModel()
        self.block = txmempool.Block.blockFromHistory(self.testBlockHeight, dbFile=dbFile)
        self.blockHeightRange = range(self.testBlockHeight, self.testBlockHeight+1)

        entries = deepcopy(self.block.entries)
        for entry in entries.values():
            del entry['leadTime'], entry['feeRate'], entry['inBlock']

        self.currPool = entries

    def test_processBlocks(self):
        processedBlock = txmempool.TxMempool.processBlocks(self.model,self.blockHeightRange,
            self.currPool, deepcopy(self.currPool), self.block.time)[0]
        self.assertEqual(processedBlock, self.block)

    def test_processEmptyMempool(self):
        self.block.entries = {}
        processedBlock = txmempool.TxMempool.processBlocks(self.model,self.blockHeightRange,
            {},{},self.block.time)[0]
        self.assertEqual(processedBlock, self.block)



if __name__ == '__main__':
    unittest.main()