from __future__ import division

import os
import unittest
import threading
import logging
from random import expovariate, random, seed
from math import log

from feemodel.config import datadir
from feemodel.txmempool import MemBlock, MemEntry
from feemodel.estimate import RectEstimator, ExpEstimator
from feemodel.tests.config import memblock_dbfile as dbfile, txref
from feemodel.simul.simul import SimMempool
from feemodel.simul.txsources import DEFAULT_PRINT_FEERATES as feerates

logging.basicConfig(level=logging.DEBUG)
seed(0)

tmpdbfile = os.path.join(datadir, '_tmp.db')

blockrange = (333931, 333954)
gen_blockrange = (0, 100)

if os.path.exists(tmpdbfile):
    os.remove(tmpdbfile)


def populate_testdb():
    t = 0
    mempool = SimMempool({})
    tx_emitter = txref.get_emitter(mempool)
    print("txref is {}".format(txref))
    for height in range(*gen_blockrange):
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
        b.blocksize = sum([entry.size for entry in mempool_entries.values()])
        b.entries = entries
        b.write(tmpdbfile, 2000)
        mempool.reset()


populate_testdb()
_dum, txref_rates = txref.get_byterates(feerates=feerates)


class RectEstimatorTest(unittest.TestCase):

    def test_basic(self):
        print("Starting RectEstimator test")
        tr = RectEstimator(maxsamplesize=10000)
        tr.start(blockrange, dbfile=dbfile)
        print(tr)
        uniquetxs = set([(tx.feerate, tx.size) for tx in tr.txsample])
        print("unique ratio is {}".format(len(uniquetxs) / len(tr.txsample)))
        tr.print_rates()

    def test_limit_sample(self):
        maxsamplesize = 1000
        tr = RectEstimator(maxsamplesize=maxsamplesize)
        tr.start(blockrange, dbfile=dbfile)
        print(tr)
        tr.print_rates()

    def test_stop(self):
        tr = RectEstimator(maxsamplesize=1000)
        stopflag = threading.Event()
        threading.Timer(0.01, stopflag.set).start()
        self.assertRaises(StopIteration, tr.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)


class ExpEstimatorTest(unittest.TestCase):

    def test_basic(self):
        print("Starting ExpEstimator test")
        tr = ExpEstimator(3600)
        tr.start(blockrange[1]-1, dbfile=dbfile)
        print(tr)
        uniquetxs = set([(tx.feerate, tx.size) for tx in tr.txsample])
        print("unique ratio is {}".format(len(uniquetxs) / len(tr.txsample)))
        tr.print_rates()

    def test_stop(self):
        tr = ExpEstimator(3600)
        stopflag = threading.Event()
        threading.Timer(0.01, stopflag.set).start()
        with self.assertRaises(StopIteration):
            tr.start(blockrange[1]-1, stopflag=stopflag, dbfile=dbfile)


class SamplingTest(unittest.TestCase):
    '''Generate and re-estimate.'''

    def test_A(self):
        tr = RectEstimator(maxsamplesize=100000)
        print("Starting estimation from generated...")
        tr.start(gen_blockrange, dbfile=tmpdbfile)
        print("Rect estimation from generated:")
        print("===============================")
        print("Test:")
        print(tr)
        tr.print_rates()
        print("Target:")
        print(txref)
        txref.print_rates()

        _dum, byterates = tr.get_byterates(feerates=feerates)
        for test, target in zip(byterates, txref_rates):
            diff = abs(log(test) - log(target))
            self.assertLess(diff, 0.2)
            print("Diff is {}".format(diff))
        diff = abs(log(tr.txrate) - log(txref.txrate))
        print("txrate log diff is {}".format(diff))
        self.assertLess(diff, 0.1)

    def test_B(self):
        tr = ExpEstimator(86400)
        print("Starting estimation from generated...")
        tr.start(gen_blockrange[-1]-1, dbfile=tmpdbfile)
        print("Exp estimation from generated:")
        print("===============================")
        print("Test:")
        print(tr)
        tr.print_rates()
        print("Target:")
        print(txref)
        txref.print_rates()

        _dum, byterates = tr.get_byterates(feerates=feerates)
        for test, target in zip(byterates, txref_rates):
            diff = abs(log(test) - log(target))
            self.assertLess(diff, 0.2)
            print("Diff is {}".format(diff))
        diff = abs(log(tr.txrate) - log(txref.txrate))
        print("txrate log diff is {}".format(diff))
        self.assertLess(diff, 0.1)

        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)


if __name__ == '__main__':
    unittest.main()
