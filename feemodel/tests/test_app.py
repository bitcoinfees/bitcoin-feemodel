import unittest
import sqlite3
import multiprocessing
import threading
from time import sleep, time
from pprint import pprint

from feemodel.tests.config import (test_memblock_dbfile as memblock_dbfile,
                                   mk_tmpdatadir, rm_tmpdatadir, txref)
from feemodel.tests.pseudoproxy import install, proxy

from feemodel.txmempool import MemBlock, MEMBLOCK_DBFILE
from feemodel.app.simonline import SimOnline
from feemodel.app.predict import Prediction
from feemodel.app.txrate import TxRateOnlineEstimator
from feemodel.config import config, MINRELAYTXFEE
from feemodel.apiclient import APIClient
from feemodel.app.main import main

import feemodel.txmempool as txmempool

install()
poll_period = config.getfloat("txmempool", "poll_period")
apiclient = APIClient()


class TxRateOnlineTests(unittest.TestCase):

    def setUp(self):
        self.txonline = TxRateOnlineEstimator()
        self.txonline.tx_estimator = txref

    def test_stats(self):
        stats = self.txonline.get_stats()
        feerates = stats['cumbyterate']['feerates']
        byterates = stats['cumbyterate']['byterates']
        for feerate, byterate in zip(feerates, byterates):
            self.assertAlmostEqual(byterate, self.get_refbyterate(feerate))
        self.assertAlmostEqual(stats['ratewithfee'],
                               self.get_refbyterate(MINRELAYTXFEE))

    def get_refbyterate(self, feerate):
        txsample = txref.txsample
        txrate = txref.txrate
        return sum([tx.size for tx in txsample
                    if tx.feerate >= feerate])*txrate/len(txsample)


class BasicAppTests(unittest.TestCase):

    def setUp(self):
        self.time = get_mytime()
        txmempool.time = self.time
        mk_tmpdatadir()
        proxy.blockcount = 333952
        proxy.rawmempool = {}

        db = sqlite3.connect(MEMBLOCK_DBFILE)
        with db:
            db.execute("DELETE FROM blocks WHERE height=333953")
            db.execute("DELETE FROM txs WHERE height=333953")
        db.close()

    def test_A(self):
        """Basic tests."""
        config.set("app", "pools_minblocks", "1")
        sim = SimOnline()
        print("Starting test A thread.")
        with sim.context_start():
            while not sim.txonline:
                sleep(0.1)
            while not sim.transient.stats:
                sleep(0.1)
            transient_stats = sim.transient.stats
            print(transient_stats.expectedwaits)
            lowest_feerate = transient_stats.feepoints[0]
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
            pred_db = Prediction.from_db(
                config.getint("app", "predict_block_halflife"))
            self.assertEqual(pred_db.pval_ecdf, sim.prediction.pval_ecdf)
            self.assertEqual(sum(sim.prediction.pvalcounts), 79)

            poolsref = sim.poolsonline.get_pools()
            predictionref = sim.prediction

        # Test loading of predicts and pools
        config.set("app", "pools_minblocks", "432")
        sim = SimOnline()
        with sim.context_start():
            sleep(1)
            self.assertTrue(sim.poolsonline)
            self.assertEqual(poolsref, sim.poolsonline.get_pools())
            self.assertEqual(predictionref, sim.prediction)

    def test_B(self):
        """No memblocks."""
        config.set("app", "pools_minblocks", "432")
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
        rm_tmpdatadir()


class AppAPITests(unittest.TestCase):

    def setUp(self):
        mk_tmpdatadir()
        self.time = get_mytime()
        txmempool.time = self.time
        config.set("app", "pools_minblocks", "1")

        db = sqlite3.connect(MEMBLOCK_DBFILE)
        with db:
            db.execute("DELETE FROM blocks WHERE height=333953")
            db.execute("DELETE FROM txs WHERE height=333953")
        db.close()

    def test_A(self):
        process = multiprocessing.Process(target=self.maintarget)
        process.start()
        while True:
            try:
                apiclient.set_loglevel("debug")
                apiclient.get_txrate()
            except Exception:
                sleep(1)
            else:
                break
        pprint(apiclient.get_pools())
        pprint(apiclient.get_mempool())
        pprint(apiclient.get_transient())
        pprint(apiclient.get_prediction())
        sleep(30)
        pprint(apiclient.get_pools())
        pprint(apiclient.get_mempool())
        pprint(apiclient.get_transient())
        pprint(apiclient.get_prediction())
        pprint(apiclient.get_txrate())
        pprint(apiclient.estimatefee(12))
        pe = apiclient.get_poolsobj()
        print(pe)
        print("Terminating main process.")
        process.terminate()
        process.join()

    def maintarget(self):
        threading.Thread(target=self.proxyschedule).start()
        sleep(1)
        main()

    def proxyschedule(self):
        proxy.blockcount = 333952
        proxy.set_rawmempool(333953)
        sleep(10)
        print("*** Incrementing block count ***")
        proxy.blockcount += 1

    def tearDown(self):
        rm_tmpdatadir()


def get_mytime():
    starttime = time()
    b = MemBlock.read(333952, dbfile=memblock_dbfile)
    reftime = b.time

    def mytime():
        return time() - starttime + reftime

    return mytime


if __name__ == '__main__':
    unittest.main()
