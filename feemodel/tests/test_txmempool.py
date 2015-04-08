import unittest
import sqlite3
import os
import logging

from feemodel.config import datadir
from feemodel.txmempool import TxMempool, MemBlock, MempoolState

from feemodel.tests.config import memblock_dbfile as dbfile
from feemodel.tests.pseudoproxy import (proxy, install,
                                        rawmempool_from_mementries)

proxy.set_rawmempool(333931)
install()

blocks_to_keep = 10
logging.basicConfig(level=logging.DEBUG)

tmpdbfile = os.path.join(datadir, '_tmp_test.db')


class WriteReadTests(unittest.TestCase):
    def setUp(self):
        self.test_blockheight = 333931
        self.db = None
        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)

    def test_writeread(self):
        '''Tests that mempool entry is unchanged upon write/read.'''
        memblock = MemBlock.read(333931, dbfile=dbfile)
        memblock.write(dbfile=tmpdbfile, blocks_to_keep=2016)
        memblock_read = MemBlock.read(333931, dbfile=tmpdbfile)
        print(memblock_read)
        self.assertEqual(memblock_read, memblock)

    def test_writereadempty(self):
        '''Tests write/read of empty entries dict'''
        memblock = MemBlock.read(self.test_blockheight, dbfile=dbfile)
        memblock.entries = {}
        memblock.write(dbfile=tmpdbfile, blocks_to_keep=2016)
        memblock_read = MemBlock.read(self.test_blockheight, dbfile=tmpdbfile)
        self.assertEqual(memblock_read, memblock)

    def test_write_uninitialized(self):
        '''Test write of uninitialized MemBlock.'''
        memblock = MemBlock()
        with self.assertRaises(ValueError):
            memblock.write(dbfile=tmpdbfile, blocks_to_keep=2016)

    def test_deletehistory(self):
        '''Test that history is deleted according to retention policy.'''
        memblocks = [MemBlock.read(height, dbfile=dbfile)
                     for height in range(333931, 333953)]

        for memblock in memblocks:
            if memblock:
                memblock.write(dbfile=tmpdbfile, blocks_to_keep=blocks_to_keep)

        block_list = MemBlock.get_heights(dbfile=tmpdbfile)
        self.assertEqual(len(block_list), blocks_to_keep)

    def test_duplicate_writes(self):
        block = MemBlock.read(333931, dbfile=dbfile)
        block.write(tmpdbfile, 100)
        self.assertRaises(sqlite3.IntegrityError, block.write, tmpdbfile, 100)
        self.db = sqlite3.connect(tmpdbfile)
        txlist = self.db.execute('SELECT * FROM txs WHERE blockheight=?',
                                 (333931,))
        txids = [tx[1] for tx in txlist]
        self.assertEqual(sorted(set(txids)), sorted(txids))
        block_read = MemBlock.read(333931, dbfile=tmpdbfile)
        self.assertEqual(block, block_read)

    def test_read_uninitialized(self):
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
        self.memblockref = MemBlock.read(self.test_blockheight,
                                         dbfile=dbfile)
        for entry in self.memblockref.entries.values():
            # This test data was from an old version where leadtime was not
            # an integer.
            entry.leadtime = int(entry.leadtime)
            entry.isconflict = False
        self.testrawmempool = rawmempool_from_mementries(
            self.memblockref.entries)
        self.mempool = TxMempool()

    def test_process_blocks(self):
        prevstate = MempoolState(self.test_blockheight-1, self.testrawmempool)
        newstate = MempoolState(self.test_blockheight, self.testrawmempool)
        memblocks = self.mempool.process_blocks(
            prevstate, newstate, self.memblockref.time)
        self.assertEqual(memblocks[0], self.memblockref)

    def test_process_empty_mempool(self):
        self.memblockref.entries = {}
        prevstate = MempoolState(self.test_blockheight-1, {})
        newstate = MempoolState(self.test_blockheight, {})
        memblocks = self.mempool.process_blocks(
            prevstate, newstate, self.memblockref.time)
        self.assertEqual(memblocks[0], self.memblockref)

    def test_multipleblocks(self):
        print("\nMultiple blocks test\n====================")
        prevstate = MempoolState(self.test_blockheight-1, self.testrawmempool)
        newstate = MempoolState(self.test_blockheight+2, self.testrawmempool)
        memblocks = self.mempool.process_blocks(
            prevstate, newstate, self.memblockref.time)

        previnblock = None
        for b in memblocks:
            self.assertTrue(all([not entry.isconflict
                                 for entry in b.entries.values()]))
            if previnblock:
                # Check that inblock txs are removed from entries before
                # next block is processed.
                print("Checking for no inblock overlap...")
                self.assertFalse(set(previnblock) & set(b.entries))

            previnblock = [txid for txid, entry in b.entries.items()
                           if entry.inblock]
            print b
        print("{} entries remaining.".format(len(b.entries)))
        print("====================")

    def test_multipleblocks_conflicts(self):
        print("\nMultiple blocks conflicts test\n====================")
        prevstate = MempoolState(self.test_blockheight-1, self.testrawmempool)
        newstate = MempoolState(self.test_blockheight+2, {})
        memblocks = self.mempool.process_blocks(
            prevstate, newstate, self.memblockref.time)
        for idx, b in enumerate(memblocks):
            self.assertTrue(all([
                not entry.isconflict for entry in b.entries.values()
                if entry.inblock]))
            if idx == 0:
                conflicts = [txid for txid, entry in b.entries.items()
                             if entry.isconflict]
                print("{} conflicts.".format(len(conflicts)))
            else:
                self.assertFalse(set(conflicts) & set(b.entries))
                if idx == len(memblocks)-1:
                    self.assertTrue(
                        all([entry.inblock for entry in b.entries.values()]))
            print b
        print("====================")


if __name__ == '__main__':
    unittest.main()
