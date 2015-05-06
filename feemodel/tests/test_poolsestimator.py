import os
import unittest
import threading
from copy import copy

from feemodel.tests.config import (test_memblock_dbfile as dbfile, poolsref,
                                   tmpdatadir_context)
from feemodel.tests.pseudoproxy import install

from feemodel.util import save_obj, load_obj
from feemodel.estimate import PoolsEstimator


install()
blockrange = (333931, 333954)

pe = PoolsEstimator()
pe.start(blockrange, dbfile=dbfile)


class PoolEstimateTest(unittest.TestCase):

    def test_basic(self):
        print("pools is: ")
        print(repr(pe))
        print(pe)
        print("poolsref is: ")
        print(repr(poolsref))
        print(poolsref)
        self.assertEqual(poolsref, pe)

    def test_saveload(self):
        with tmpdatadir_context() as datadir:
            savefile = os.path.join(datadir, '_test_tmp.pickle')
            save_obj(pe, savefile)
            pe_load = load_obj(savefile)
            self.assertEqual(pe_load, pe)

    def test_redorange(self):
        pe_tmp = copy(pe)
        pe_tmp.start(blockrange, dbfile=dbfile)
        self.assertEqual(pe_tmp, pe)

    def test_smallrange(self):
        pe_tmp = copy(pe)
        pe_tmp.start((333931, 333940), dbfile=dbfile)
        self.assertNotEqual(pe_tmp, pe)

    def test_stop(self):
        stopflag = threading.Event()
        pe = PoolsEstimator()
        threading.Timer(1, stopflag.set).start()
        self.assertRaises(StopIteration, pe.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)


if __name__ == '__main__':
    unittest.main()
