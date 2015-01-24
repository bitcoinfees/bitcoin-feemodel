import unittest
import os
import logging
from copy import deepcopy

from feemodel.util import proxy
from feemodel.txmempool import TxMempool, MemBlock

keep_history = 10
logging.basicConfig(level=logging.DEBUG)

dbfile = 'data/test.db'
tmpdbfile = 'data/tmptest.db'


class WriteReadTests(unittest.TestCase):
    def setUp(self):
        self.test_blockheight = 333931
        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)

    def test_writeread(self):
        '''Tests that mempool entry is unchanged upon write/read.'''
        entries = proxy.getrawmempool(verbose=True)
        for entry in entries.values():
            entry['leadtime'] = 0
            entry['feerate'] = 0
            entry['inblock'] = False
            entry['isconflict'] = False

        memblock = MemBlock(entries, 1000, 1000, 1000)
        memblock.write(dbfile=tmpdbfile, keep_history=2016)
        memblock_read = MemBlock.read(1000, dbfile=tmpdbfile)
        print(memblock_read)
        self.assertEqual(memblock_read.entries, entries)

    def test_writereadempty(self):
        '''Tests write/read of empty entries dict'''
        memblock = MemBlock.read(self.test_blockheight, dbfile=dbfile)
        memblock.entries = {}
        memblock.write(dbfile=tmpdbfile)
        memblock_read = MemBlock.read(self.test_blockheight, dbfile=tmpdbfile)
        self.assertEqual(memblock_read, memblock)

    def test_deletehistory(self):
        '''Test that history is deleted according to retention policy.'''
        memblocks = [MemBlock.read(height, dbfile=dbfile)
                     for height in range(333931, 333953)]

        for memblock in memblocks:
            if memblock:
                memblock.write(dbfile=tmpdbfile, keep_history=keep_history)

        block_list = MemBlock.get_block_list(dbfile=tmpdbfile)
        self.assertEqual(len(block_list), keep_history)

    def tearDown(self):
        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)


class ProcessBlocksTest(unittest.TestCase):
    def setUp(self):
        self.test_blockheight = 333931
        self.memblock = MemBlock.read(self.test_blockheight,
                                      dbfile=dbfile)
        self.blockheight_range = range(self.test_blockheight,
                                       self.test_blockheight+1)

        entries = deepcopy(self.memblock.entries)
        for entry in entries.values():
            del entry['leadtime'], entry['feerate']
            del entry['inblock'], entry['isconflict']

        self.entries_prev = entries

    def test_process_blocks(self):
        processed_memblock = TxMempool.process_blocks(
            TxMempool(), self.blockheight_range, self.entries_prev,
            deepcopy(self.entries_prev), self.memblock.time)[0]
        self.assertEqual(processed_memblock, self.memblock)

    def test_process_empty_mempool(self):
        self.memblock.entries = {}
        processed_memblock = TxMempool.process_blocks(
            TxMempool(), self.blockheight_range,
            {}, {}, self.memblock.time)[0]
        self.assertEqual(processed_memblock, self.memblock)


if __name__ == '__main__':
    unittest.main()
