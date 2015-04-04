import logging
import os
import unittest
import threading
from copy import deepcopy
from time import sleep
from random import seed

from feemodel.tests.pseudoproxy import install

from feemodel.util import save_obj, load_obj
from feemodel.estimate import PoolsEstimator
from feemodel.config import datadir

from feemodel.tests.config import memblock_dbfile as dbfile, poolsref

install()
seed(0)
savefile = os.path.join(datadir, '_test_tmp.pickle')
if os.path.exists(savefile):
    os.remove(savefile)
logging.basicConfig(level=logging.DEBUG)

blockrange = (333931, 333954)

pe = PoolsEstimator()
pe.start(blockrange, dbfile=dbfile)


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


class PoolEstimateTest(unittest.TestCase):

    def test_basic(self):
        print("pools is: ")
        pe.print_pools()
        print(pe)
        pools = pe.get_pools()
        print(pools)
        print("poolsref is: ")
        poolsref.print_pools()
        self.assertEqual(poolsref, pe)

    def test_saveload(self):
        save_obj(pe, savefile)
        pe_load = load_obj(savefile)
        self.assertEqual(pe_load, pe)

    def test_redorange(self):
        pe_tmp = deepcopy(pe)
        pe_tmp.start(blockrange, dbfile=dbfile)
        self.assertEqual(pe_tmp, pe)

    def test_smallrange(self):
        pe_tmp = deepcopy(pe)
        pe_tmp.start((333931, 333940), dbfile=dbfile)
        self.assertNotEqual(pe_tmp, pe)

    def test_stop(self):
        stopflag = threading.Event()
        pe = PoolsEstimator()
        stopthread = threading.Thread(target=delayed_stop, args=(stopflag, 1))
        stopthread.start()
        self.assertRaises(StopIteration, pe.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()

    def tearDown(self):
        if os.path.exists(savefile):
            os.remove(savefile)


if __name__ == '__main__':
    unittest.main()
