import unittest
import os
import logging
import threading
from copy import deepcopy
from time import sleep
from feemodel.util import save_obj, load_obj
from feemodel.waitmeasure import WaitMeasure

dbfile = 'data/test.db'
savefile = 'data/tmp.pickle'
feerates = range(0, 100000, 10000)
blockrange = (333931,333954)
logging.basicConfig(level=logging.DEBUG)


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


class WaitMeasureTest(unittest.TestCase):
    def setUp(self):
        self.wm = WaitMeasure(feerates)
        self.wm.calcwaits(blockrange, dbfile=dbfile)

    def test_basic(self):
        self.wm.waitstat.print_waits()

    def test_saveload(self):
        save_obj(self.wm, savefile)
        wm_load = load_obj(savefile)
        self.assertEqual(wm_load, self.wm)

    def test_widerange(self):
        wm_tmp = deepcopy(self.wm)
        self.wm.calcwaits((333900, 333990), dbfile=dbfile)
        self.assertEqual(wm_tmp, self.wm)

    def test_smallrange(self):
        wm_tmp = deepcopy(self.wm)
        self.wm.calcwaits((333931, 333940), dbfile=dbfile)
        self.assertNotEqual(wm_tmp, self.wm)

    def test_stop(self):
        stopflag = threading.Event()
        self.wm = WaitMeasure(feerates)
        stopthread = threading.Thread(target=delayed_stop, args=(stopflag, 0.1))
        stopthread.start()
        self.assertRaises(StopIteration, self.wm.calcwaits, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()

    def tearDown(self):
        if os.path.exists(savefile):
            os.remove(savefile)

if __name__ == '__main__':
    unittest.main()
