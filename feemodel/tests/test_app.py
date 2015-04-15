import os
import shutil
import unittest
import logging
from time import sleep, time
from pprint import pprint

from feemodel.txmempool import MemBlock
from feemodel.tests.pseudoproxy import install, proxy
from feemodel.tests.config import memblock_dbfile as testdb
from feemodel.app.simonline import SimOnline
from feemodel.config import datadir

import feemodel.app.simonline as simonline
import feemodel.app.txrate as apptxrate

logging.basicConfig(level=logging.DEBUG)
install()


class AppTests(unittest.TestCase):

    def setUp(self):
        self.memblock_dbfile = os.path.join(datadir, '_memblocks.db')
        self.pools_savedir = os.path.join(datadir, '_pools/')
        self.predict_savefile = os.path.join(datadir, '_savepredicts.pickle')
        self.pvals_dbfile = os.path.join(datadir, '_pvals.db')

        shutil.copyfile(testdb, self.memblock_dbfile)

        simonline.memblock_dbfile = self.memblock_dbfile
        simonline.pools_savedir = self.pools_savedir
        simonline.predict_savefile = self.predict_savefile
        simonline.pvals_dbfile = self.pvals_dbfile

        apptxrate.time = get_mytime(self.memblock_dbfile)

    def test_A(self):
        """Basic tests."""
        simonline.pools_minblocks = 1
        sim = SimOnline()
        proxy.blockcount = 333953
        proxy.on = False
        # proxy.set_rawmempool(333931)
        print("Starting test A thread.")
        with sim.context_start():
            for method in ['get_predictstats', 'get_poolstats',
                           'get_transientstats', 'get_txstats']:
                self.assertIsNone(getattr(sim, method)())
            sleep(1)
            proxy.on = True
            print("Turning proxy on.")
            while not sim.txonline:
                sleep(0.1)
            pprint(sim.get_txstats())
            while not sim.transient.stats:
                sleep(0.1)
            transient_stats = sim.transient.stats
            transient_stats.expectedwaits.print_fn()
            transient_stats.cap.print_cap()
            pprint(sim.get_txstats())
            pprint(sim.get_poolstats())

    def test_B(self):
        """No memblocks."""
        simonline.pools_minblocks = 432
        sim = SimOnline()
        proxy.blockcount = 333930
        proxy.set_rawmempool(333931)
        print("Starting test B thread.")
        with sim.context_start():
            sleep(5)
            proxy.set_rawmempool(333932)
            sleep(5)
            pprint(sim.get_txstats())

    def tearDown(self):
        for filepath in [self.memblock_dbfile,
                         self.predict_savefile,
                         self.pvals_dbfile]:
            if os.path.exists(filepath):
                os.remove(filepath)
        if os.path.exists(self.pools_savedir):
            shutil.rmtree(self.pools_savedir)


def proxyschedule():
    pass


def get_mytime(dbfile):
    starttime = time()
    b = MemBlock.read(333953, dbfile=dbfile)
    reftime = b.time

    def mytime():
        return time() - starttime + reftime

    return mytime


if __name__ == '__main__':
    unittest.main()