from __future__ import division

import os
import unittest
import threading
import logging
from random import expovariate, random
from math import log

from feemodel.tests.config import (test_memblock_dbfile as dbfile, txref,
                                   setup_tmpdatadir)
from feemodel.txmempool import MemBlock, MemEntry
from feemodel.estimate import RectEstimator, ExpEstimator
from feemodel.simul.simul import SimMempool
from feemodel.simul.txsources import DEFAULT_PRINT_FEERATES as FEERATES

logging.basicConfig(level=logging.DEBUG)


class RectEstimatorTest(unittest.TestCase):

    def setUp(self):
        self.blockrange = (333931, 333954)

    def test_basic(self):
        print("Starting RectEstimator test")
        tr = RectEstimator(maxsamplesize=10000)
        tr.start(self.blockrange, dbfile=dbfile)
        print(tr)
        uniquetxs = set([(tx.feerate, tx.size) for tx in tr.txsample])
        print("unique ratio is {}".format(len(uniquetxs) / len(tr.txsample)))
        tr.print_rates()

    def test_limit_sample(self):
        maxsamplesize = 1000
        tr = RectEstimator(maxsamplesize=maxsamplesize)
        tr.start(self.blockrange, dbfile=dbfile)
        print(tr)
        tr.print_rates()

    def test_stop(self):
        tr = RectEstimator(maxsamplesize=1000)
        stopflag = threading.Event()
        threading.Timer(0.01, stopflag.set).start()
        self.assertRaises(StopIteration, tr.start, self.blockrange,
                          stopflag=stopflag, dbfile=dbfile)


class ExpEstimatorTest(unittest.TestCase):

    def setUp(self):
        self.blockrange = (333931, 333954)

    def test_basic(self):
        print("Starting ExpEstimator test")
        tr = ExpEstimator(3600)
        tr.start(self.blockrange[1]-1, dbfile=dbfile)
        print(tr)
        uniquetxs = set([(tx.feerate, tx.size) for tx in tr.txsample])
        print("unique ratio is {}".format(len(uniquetxs) / len(tr.txsample)))
        tr.print_rates()

    def test_stop(self):
        tr = ExpEstimator(3600)
        stopflag = threading.Event()
        threading.Timer(0.01, stopflag.set).start()
        with self.assertRaises(StopIteration):
            tr.start(self.blockrange[1]-1, stopflag=stopflag, dbfile=dbfile)


class SamplingTest(unittest.TestCase):
    '''Generate and re-estimate.'''

    def test_A(self):
        _dum, txref_rates = txref.get_byterates(feerates=FEERATES)
        with setup_tmpdatadir() as datadir:
            # RectEstimator
            self.gen_blockrange = (0, 100)
            self.tmpdbfile = os.path.join(datadir, '_tmp.db')
            self.populate_testdb()

            tr = RectEstimator(maxsamplesize=100000)
            print("Starting estimation from generated...")
            tr.start(self.gen_blockrange, dbfile=self.tmpdbfile)
            print("Rect estimation from generated:")
            print("===============================")
            print("Test:")
            print(tr)
            tr.print_rates()
            print("Target:")
            print(txref)
            txref.print_rates()

            _dum, byterates = tr.get_byterates(feerates=FEERATES)
            for test, target in zip(byterates, txref_rates):
                diff = abs(log(test) - log(target))
                self.assertLess(diff, 0.2)
                print("Diff is {}".format(diff))
            diff = abs(log(tr.txrate) - log(txref.txrate))
            print("txrate log diff is {}".format(diff))
            self.assertLess(diff, 0.1)

            # ExpEstimator
            tr = ExpEstimator(86400)
            print("Starting estimation from generated...")
            tr.start(self.gen_blockrange[-1]-1, dbfile=self.tmpdbfile)
            print("Exp estimation from generated:")
            print("===============================")
            print("Test:")
            print(tr)
            tr.print_rates()
            print("Target:")
            print(txref)
            txref.print_rates()

            _dum, byterates = tr.get_byterates(feerates=FEERATES)
            for test, target in zip(byterates, txref_rates):
                diff = abs(log(test) - log(target))
                self.assertLess(diff, 0.2)
                print("Diff is {}".format(diff))
            diff = abs(log(tr.txrate) - log(txref.txrate))
            print("txrate log diff is {}".format(diff))
            self.assertLess(diff, 0.1)

    def populate_testdb(self):
        t = 0
        mempool = SimMempool({})
        tx_emitter = txref.get_emitter(mempool)
        print("txref is {}".format(txref))
        for height in range(*self.gen_blockrange):
            blockinterval = expovariate(1/600)
            t += blockinterval
            tx_emitter(blockinterval)
            mempool_entries = mempool.get_entries()
            entries = {}
            for txid, entry in mempool_entries.items():
                # Dummy fields
                mementry = MemEntry()
                mementry.startingpriority = 0
                mementry.currentpriority = 0
                mementry.fee = entry.feerate*entry.size
                mementry.feerate = entry.feerate
                mementry.leadtime = 0
                mementry.isconflict = False
                mementry.inblock = False

                # Relevant fields
                mementry.time = t - random()*blockinterval
                mementry.height = height
                entries[str(height)+txid] = mementry
                mementry.size = entry.size

            b = MemBlock()
            b.height = height - 1
            b.blockheight = height
            b.time = t
            b.blocksize = sum([
                entry.size for entry in mempool_entries.values()])
            b.entries = entries
            b.write(self.tmpdbfile, 2000)
            mempool.reset()


if __name__ == '__main__':
    unittest.main()
