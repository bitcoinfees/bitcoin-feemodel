import os
import shutil
import unittest
import logging
import sqlite3
from time import sleep, time
from pprint import pprint

from feemodel.txmempool import MemBlock
from feemodel.tests.pseudoproxy import install, proxy
from feemodel.tests.config import memblock_dbfile as testdb
from feemodel.app.simonline import SimOnline, predict_block_halflife
from feemodel.app.predict import Prediction
from feemodel.config import datadir, poll_period

import feemodel.app.simonline as simonline
import feemodel.txmempool as txmempool

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
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

        self.time = get_mytime(self.memblock_dbfile)
        txmempool.time = self.time
        db = sqlite3.connect(self.memblock_dbfile)
        with db:
            db.execute("DELETE FROM blocks WHERE height=333953")
            db.execute("DELETE FROM txs WHERE height=333953")
        db.close()

    def test_A(self):
        """Basic tests."""
        simonline.pools_minblocks = 1
        sim = SimOnline()
        proxy.blockcount = 333952
        print("Starting test A thread.")
        with sim.context_start():
            for method in ['get_predictstats', 'get_poolstats',
                           'get_transientstats', 'get_txstats']:
                self.assertIsNone(getattr(sim, method)())
            while not sim.txonline:
                sleep(0.1)
            while not sim.transient.stats:
                sleep(0.1)
            transient_stats = sim.transient.stats
            transient_stats.expectedwaits.print_fn()
            transient_stats.cap.print_cap()
            lowest_feerate = transient_stats.feerates[0]
            print("*** Setting rawmempool to 333931 ***")
            proxy.set_rawmempool(333953)
            sleep(poll_period)
            for txid, predict in sim.prediction.predicts.items():
                if predict is not None:
                    predict.entrytime = self.time() - 600
                else:
                    entry = sim.state.entries[txid]
                    self.assertTrue(
                        entry.is_high_priority() or
                        entry.depends or
                        entry.feerate < lowest_feerate)
            print("*** Incrementing blockcount ***")
            proxy.blockcount += 1
            proxy.rawmempool = {}
            sleep(poll_period*2)
            predictstats = sim.get_predictstats()
            pprint(zip(*predictstats['pval_ecdf']))
            print("p-distance is {}".format(predictstats['pdistance']))
            pred_db = Prediction.from_db(predict_block_halflife,
                                         dbfile=self.pvals_dbfile)
            self.assertEqual(pred_db.pval_ecdf, sim.prediction.pval_ecdf)

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


def get_mytime(dbfile):
    starttime = time()
    b = MemBlock.read(333953, dbfile=dbfile)
    reftime = b.time

    def mytime():
        return time() - starttime + reftime

    return mytime


if __name__ == '__main__':
    unittest.main()
