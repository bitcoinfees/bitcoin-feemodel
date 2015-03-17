import unittest
import sqlite3
import os
import logging
from copy import deepcopy

from feemodel.util import proxy
from feemodel.txmempool import TxMempool, MemBlock, MemEntry

keep_history = 10
logging.basicConfig(level=logging.DEBUG)

dbfile = 'data/test.db'
tmpdbfile = 'data/tmptest.db'


class WriteReadTests(unittest.TestCase):
    def setUp(self):
        self.test_blockheight = 333931
        self.db = None
        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)

    def test_writeread(self):
        '''Tests that mempool entry is unchanged upon write/read.'''
        rawmempool = proxy.getrawmempool(verbose=True)
        entries = {
            txid: MemEntry(rawentry) for txid, rawentry in rawmempool.items()
        }
        for entry in entries.values():
            entry.inblock = False
            entry.leadtime = 0
            entry.isconflict = False
        memblock = MemBlock()
        memblock.entries = entries
        memblock.height = 1000
        memblock.size = 1000
        memblock.time = 1000
        memblock.write(dbfile=tmpdbfile, keep_history=2016)
        memblock_read = MemBlock.read(1000, dbfile=tmpdbfile)
        print(memblock_read)
        self.assertEqual(memblock_read.entries, entries)

    def test_writereadempty(self):
        '''Tests write/read of empty entries dict'''
        memblock = MemBlock.read(self.test_blockheight, dbfile=dbfile)
        memblock.entries = {}
        memblock.write(dbfile=tmpdbfile, keep_history=2016)
        memblock_read = MemBlock.read(self.test_blockheight, dbfile=tmpdbfile)
        self.assertEqual(memblock_read, memblock)

    def test_deletehistory(self):
        '''Test that history is deleted according to retention policy.'''
        memblocks = [MemBlock.read(height, dbfile=dbfile)
                     for height in range(333931, 333953)]

        for memblock in memblocks:
            if memblock:
                memblock.write(dbfile=tmpdbfile, keep_history=keep_history)

        block_list = MemBlock.get_heights(dbfile=tmpdbfile)
        self.assertEqual(len(block_list), keep_history)

    def test_duplicate_writes(self):
        block = MemBlock.read(333931, dbfile=dbfile)
        block.write(tmpdbfile, 100)
        self.assertRaises(Exception, block.write, tmpdbfile, 100)
        self.db = sqlite3.connect(tmpdbfile)
        txlist = self.db.execute('SELECT * FROM txs WHERE blockheight=?',
                                 (333931,))
        txids = [tx[1] for tx in txlist]
        self.assertEqual(sorted(set(txids)), sorted(txids))
        block_read = MemBlock.read(333931, dbfile=tmpdbfile)
        self.assertEqual(block, block_read)

    def read_uninitialized(self):
        '''Read from a db that has not been initialized.'''
        block = MemBlock.read(333931, dbfile='nonsense.db')
        self.assertIsNone(block)
        heights = MemBlock.get_heights(dbfile='nonsense.db')
        self.assertEqual([], heights)

    def tearDown(self):
        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)
        if self.db:
            self.db.close()


class ProcessBlocksTest(unittest.TestCase):
    def setUp(self):
        self.test_blockheight = 333931
        self.memblock = MemBlock.read(self.test_blockheight,
                                      dbfile=dbfile)
        for entry in self.memblock.entries.values():
            entry.leadtime = None
            entry.isconflict = False

        self.blockheight_range = range(self.test_blockheight,
                                       self.test_blockheight+1)

        self.entries = deepcopy(self.memblock.entries)
        for entry in self.entries.values():
            entry.isconflict = None
            entry.inblock = None

    def test_process_blocks(self):
        processed_memblock = TxMempool.process_blocks(
            TxMempool(), self.blockheight_range, self.entries,
            set(self.entries), 1)[0]
        processed_memblock.time = self.memblock.time
        for entry in processed_memblock.entries.values():
            entry.leadtime = None
        self.assertEqual(processed_memblock, self.memblock)

    def test_process_empty_mempool(self):
        self.memblock.entries = {}
        processed_memblock = TxMempool.process_blocks(
            TxMempool(), self.blockheight_range, {}, set(), 1)[0]
        processed_memblock.time = self.memblock.time
        self.assertEqual(processed_memblock, self.memblock)

    def test_multipleblocks(self):
        print("\nMultiple blocks test\n====================")
        memblocks = TxMempool.process_blocks(
            TxMempool(), range(self.test_blockheight, self.test_blockheight+2),
            self.entries, set(self.entries), 1)
        previnblock = None
        for m in memblocks:
            self.assertTrue(all([not entry.isconflict
                                 for entry in m.entries.values()]))
            if previnblock:
                print("Checking for no inblock overlap...")
                self.assertFalse(set(previnblock) & set(m.entries))

            previnblock = [entry for entry in m.entries.values()
                           if entry.inblock]
            print m
        print("====================")

    def test_multipleblocks_conflicts(self):
        print("\nMultiple blocks conflicts test\n====================")
        memblocks = TxMempool.process_blocks(
            TxMempool(), range(self.test_blockheight, self.test_blockheight+2),
            self.entries, set(), 1)
        for idx, m in enumerate(memblocks):
            if idx == 0:
                conflicts = [txid for txid, entry in m.entries.items()
                             if entry.isconflict]
                for txid in conflicts:
                    self.assertFalse(entry.inblock)
            elif idx == len(memblocks) - 1:
                self.assertTrue(all([
                    entry.inblock for entry in m.entries.values()]))
            else:
                self.assertTrue([txid not in m.entries for txid in conflicts])
                self.assertTrue(all([not entry.isconflict
                                     for entry in m.entries.values()]))
            print m
        print("====================")


if __name__ == '__main__':
    unittest.main()
