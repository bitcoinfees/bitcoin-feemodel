import logging
import os
import unittest
import threading
from copy import deepcopy
from time import sleep
from feemodel.util import save_obj, load_obj
from feemodel.simul import SimPools
from feemodel.estimate import PoolsEstimator

logging.basicConfig(level=logging.DEBUG)
dbfile = 'data/test.db'
savefile = 'data/tmp.pickle'

blockrange = (333931, 333954)

pe = PoolsEstimator()
pe.start(blockrange, dbfile=dbfile)


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


class PoolEstimateTest(unittest.TestCase):
    def setUp(self):
        self.pe = pe

    def test_basic(self):
        self.pe.print_pools()
        print(self.pe)

    def test_saveload(self):
        save_obj(self.pe, savefile)
        pe_load = load_obj(savefile)
        self.assertEqual(pe_load, self.pe)

    def test_redorange(self):
        pe_tmp = deepcopy(self.pe)
        pe_tmp.start(blockrange, dbfile=dbfile)
        self.assertEqual(pe_tmp, self.pe)

    def test_smallrange(self):
        pe_tmp = deepcopy(self.pe)
        pe_tmp.start((333931, 333940), dbfile=dbfile)
        self.assertNotEqual(pe_tmp, self.pe)

    def test_stop(self):
        stopflag = threading.Event()
        self.pe = PoolsEstimator()
        stopthread = threading.Thread(target=delayed_stop, args=(stopflag, 1))
        stopthread.start()
        self.assertRaises(StopIteration, self.pe.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()

    def tearDown(self):
        if os.path.exists(savefile):
            os.remove(savefile)


if __name__ == '__main__':
    unittest.main()
