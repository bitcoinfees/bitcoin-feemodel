import unittest
import sqlite3
import os
from copy import copy
from time import sleep
from pprint import pprint
from operator import itemgetter

from feemodel.tests.config import (mk_tmpdatadir, rm_tmpdatadir,
                                   test_memblock_dbfile as dbfile)
from feemodel.txmempool import (TxMempool, MemBlock, MempoolState, MemEntry,
                                get_mempool_state)
from feemodel.tests.pseudoproxy import (proxy, install,
                                        rawmempool_from_mementries)
from feemodel.config import MINRELAYTXFEE

install()


class BasicTests(unittest.TestCase):

    def setUp(self):
        proxy.set_rawmempool(333931)
        proxy.blockcount = 333930

    def test_mementry(self):
        # Test creation and copying.
        # Most importantly, that a new 'depends' object was created.
        rawentries = proxy.getrawmempool()
        rawentry = rawentries.values()[0]
        entry = MemEntry.from_rawentry(rawentry)
        self.assertEqual(entry.depends, rawentry['depends'])
        self.assertIsNot(entry.depends, rawentry['depends'])
        entry_cpy = copy(entry)
        self.assertEqual(entry.depends, entry_cpy.depends)
        self.assertIsNot(entry.depends, entry_cpy.depends)

    def test_mempoolstate(self):
        state = get_mempool_state()
        state_cpy = copy(state)
        self.assertEqual(state, state_cpy)
        self.assertIsNot(state, state_cpy)
        self.assertIsNot(state.entries, state_cpy.entries)
        for txid in state.entries:
            self.assertIsNot(state.entries[txid].depends,
                             state_cpy.entries[txid].depends)

        d = state - state_cpy
        self.assertEqual(d.height, 0)
        self.assertEqual(d.time, 0)
        self.assertEqual(len(d.entries), 0)

        state.entries['test'] = MemEntry()
        d = state - state_cpy
        self.assertEqual(d.height, 0)
        self.assertEqual(d.time, 0)
        self.assertEqual(len(d.entries), 1)
        self.assertEqual(d.entries['test'], state.entries['test'])

    def test_mempoolstate_stats(self):
        state = get_mempool_state()
        stats = state.get_stats()
        totalsize = sum([entry.size for entry in state.entries.values()])
        self.assertEqual(stats['cumsize']['size'][0], totalsize)
        for idx, feerate in enumerate(stats['cumsize']['feerates']):
            refsize = sum([entry.size for entry in state.entries.values()
                           if entry.feerate >= feerate])
            self.assertEqual(refsize, stats['cumsize']['size'][idx])
        pprint(zip(stats['cumsize']['feerates'], stats['cumsize']['size']))
        ref_size_with_fee = sum([entry.size for entry in state.entries.values()
                                 if entry.feerate >= MINRELAYTXFEE])
        self.assertEqual(ref_size_with_fee, stats['sizewithfee'])

        for entry in state.entries.values():
            entry.feerate = MINRELAYTXFEE
        stats = state.get_stats()
        totalsize = sum([entry.size for entry in state.entries.values()])
        for idx, feerate in enumerate(stats['cumsize']['feerates']):
            refsize = sum([entry.size for entry in state.entries.values()
                           if entry.feerate >= feerate])
            self.assertEqual(refsize, stats['cumsize']['size'][idx])
        pprint(zip(stats['cumsize']['feerates'], stats['cumsize']['size']))
        self.assertEqual(totalsize, stats['sizewithfee'])

        state.entries = {}
        stats = state.get_stats()
        self.assertTrue(all([size == 0 for size in stats['cumsize']['size']]))
        # self.assertFalse(stats['cumsize']['feerates'])
        # self.assertFalse(stats['cumsize']['size'])
        pprint(zip(stats['cumsize']['feerates'], stats['cumsize']['size']))
        self.assertEqual(0, stats['sizewithfee'])


class WriteReadTests(unittest.TestCase):

    def setUp(self):
        self.test_blockheight = 333931
        self.datadir = mk_tmpdatadir()

    def test_writeread(self):
        '''Tests that mempool entry is unchanged upon write/read.'''
        tmpdbfile = os.path.join(self.datadir, '_tmp.db')
        for height in MemBlock.get_heights():
            memblock = MemBlock.read(height)
            memblock.write(dbfile=tmpdbfile, blocks_to_keep=2016)
            memblock_read = MemBlock.read(height, dbfile=tmpdbfile)
            print(memblock_read)
            self.assertEqual(memblock_read, memblock)

    def test_writereadempty(self):
        '''Tests write/read of empty entries dict'''
        tmpdbfile = os.path.join(self.datadir, '_tmp.db')
        memblock = MemBlock.read(self.test_blockheight)
        memblock.entries = {}
        memblock.write(dbfile=tmpdbfile, blocks_to_keep=2016)
        memblock_read = MemBlock.read(self.test_blockheight,
                                      dbfile=tmpdbfile)
        self.assertEqual(memblock_read, memblock)

    def test_write_uninitialized(self):
        '''Test write of uninitialized MemBlock.'''
        tmpdbfile = os.path.join(self.datadir, '_tmp.db')
        memblock = MemBlock()
        with self.assertRaises(ValueError):
            memblock.write(dbfile=tmpdbfile, blocks_to_keep=2016)

    def test_deletehistory(self):
        '''Test that history is deleted according to retention policy.'''
        tmpdbfile = os.path.join(self.datadir, '_tmp.db')
        blocks_to_keep = 10
        memblocks = [MemBlock.read(height)
                     for height in range(333931, 333954)]

        for memblock in memblocks:
            if memblock:
                memblock.write(dbfile=tmpdbfile,
                               blocks_to_keep=blocks_to_keep)

        block_list = sorted(MemBlock.get_heights(dbfile=tmpdbfile))
        self.assertEqual(len(block_list), blocks_to_keep)
        self.assertEqual(block_list, list(range(333944, 333954)))

        db = None
        try:
            db = sqlite3.connect(tmpdbfile)
            blocktxsblocks = map(itemgetter(0), sorted(db.execute(
                "SELECT DISTINCT blockheight FROM blocktxs").fetchall()))
            self.assertEqual(block_list, blocktxsblocks)
            txsheights = map(itemgetter(0), sorted(db.execute(
                "SELECT DISTINCT heightremoved FROM txs").fetchall()))
            txsheights.remove(None)
            self.assertEqual(block_list, txsheights)

            # Check no duplicate txids.
            self.assertEqual(
                len(db.execute("SELECT DISTINCT txid FROM txs").fetchall()),
                len(db.execute("SELECT txid FROM txs").fetchall()))

            # Check the null-heightremoved txs
            self.assertEqual(
                db.execute("SELECT count(*) FROM txs "
                           "WHERE heightremoved IS NULL").fetchall()[0][0],
                len(filter(
                    lambda entry: not entry.inblock,
                    memblocks[-1].entries.values()))
            )
        finally:
            if db is not None:
                db.close()

    def test_duplicate_writes(self):
        tmpdbfile = os.path.join(self.datadir, '_tmp.db')
        block = MemBlock.read(333931)
        block.write(tmpdbfile, 100)
        self.assertRaises(
            sqlite3.IntegrityError, block.write, tmpdbfile, 100)
        db = None
        try:
            db = sqlite3.connect(tmpdbfile)
            txids = db.execute(
                'SELECT txid FROM txs JOIN blocktxs '
                'ON txs.id=blocktxs.txrowid WHERE blockheight=333931')
            txids = [e[0] for e in txids]
            self.assertEqual(sorted(set(txids)), sorted(txids))
            block_read = MemBlock.read(333931, dbfile=tmpdbfile)
            self.assertEqual(block, block_read)
        finally:
            if db is not None:
                db.close()

    def test_read_uninitialized(self):
        '''Read from a db that has not been initialized.'''
        block = MemBlock.read(333931, dbfile='nonsense.db')
        self.assertIsNone(block)
        heights = MemBlock.get_heights(dbfile='nonsense.db')
        self.assertEqual([], heights)

    def tearDown(self):
        rm_tmpdatadir()


class ProcessBlocksTests(unittest.TestCase):

    def setUp(self):
        self.test_blockheight = 333931
        self.memblockref = MemBlock.read(self.test_blockheight,
                                         dbfile=dbfile)
        for entry in self.memblockref.entries.values():
            entry.isconflict = False
        self.testrawmempool = rawmempool_from_mementries(
            self.memblockref.entries)
        self.mempool = TxMempool(dbfile=None)

    def test_process_blocks(self):
        prevstate = MempoolState(self.test_blockheight-1, self.testrawmempool)
        newstate = MempoolState(self.test_blockheight, self.testrawmempool)
        prevstate.time = self.memblockref.time
        memblocks = self.mempool.process_blocks(prevstate, newstate)
        self.assertEqual(memblocks[0], self.memblockref)

    def test_process_empty_mempool(self):
        self.memblockref.entries = {}
        prevstate = MempoolState(self.test_blockheight-1, {})
        newstate = MempoolState(self.test_blockheight, {})
        prevstate.time = self.memblockref.time
        memblocks = self.mempool.process_blocks(prevstate, newstate)
        self.assertEqual(memblocks[0], self.memblockref)

    def test_multipleblocks(self):
        print("\nMultiple blocks test\n====================")
        prevstate = MempoolState(self.test_blockheight-1, self.testrawmempool)
        newstate = MempoolState(self.test_blockheight+2, self.testrawmempool)
        prevstate.time = self.memblockref.time
        memblocks = self.mempool.process_blocks(prevstate, newstate)
        self.assertEqual(len(memblocks), newstate.height - prevstate.height)

        prev = None
        previnblock = None
        for idx, b in enumerate(memblocks):
            # Check for no broken deps
            self.assertTrue(all([
                all([dep in b.entries for dep in entry.depends])
                for entry in b.entries.values()]))
            self.assertEqual(b.blockheight, b.height+1)
            self.assertEqual(b.blockheight, self.test_blockheight+idx)
            self.assertTrue(all([not entry.isconflict
                                 for entry in b.entries.values()]))
            if prev:
                # Check that inblock txs are removed from entries before
                # next block is processed.
                self.assertFalse(set(previnblock) & set(b.entries))
                self.assertEqual(set(prev), set(b.entries) | set(previnblock))

            prev = b.entries.keys()
            previnblock = [txid for txid, entry in b.entries.items()
                           if entry.inblock]
            print b
        print("{} entries remaining.".format(len(b.entries)))
        print("====================")

    def test_multipleblocks_conflicts(self):
        print("\nMultiple blocks conflicts test\n====================")
        prevstate = MempoolState(self.test_blockheight-1, self.testrawmempool)
        newstate = MempoolState(self.test_blockheight+2, {})
        prevstate.time = self.memblockref.time
        memblocks = self.mempool.process_blocks(prevstate, newstate)
        self.assertEqual(len(memblocks), newstate.height - prevstate.height)

        for idx, b in enumerate(memblocks):
            self.assertEqual(b.blockheight, b.height+1)
            self.assertEqual(b.blockheight, self.test_blockheight+idx)
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


class ThreadTest(unittest.TestCase):

    def setUp(self):
        self.datadir = mk_tmpdatadir()

    def test_A(self):
        tmpdbfile = os.path.join(self.datadir, '_tmp.db')
        bref = MemBlock.read(333931)
        proxy.set_rawmempool(333931)
        proxy.blockcount = 333930
        proxy.on = False
        mempool = TxMempool(dbfile=tmpdbfile)
        print("*** Proxy is OFF ***")
        with mempool.context_start():
            sleep(50)
            proxy.on = True
            print("*** Proxy is ON ***")
            sleep(20)
            proxy.blockcount = 333931
            sleep(10)

        btest = MemBlock.read(333931, dbfile=tmpdbfile)
        # They're not equal because their times don't match.
        self.assertNotEqual(btest, bref)
        btest.time = bref.time
        for entry in bref.entries.values():
            entry.leadtime = int(entry.leadtime)
        for entry in btest.entries.values():
            entry.leadtime = btest.time - entry.time
        self.assertEqual(btest, bref)

    def tearDown(self):
        rm_tmpdatadir()


if __name__ == '__main__':
    unittest.main()
