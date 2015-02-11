import unittest
import shutil
import os
import logging
from time import sleep


import feemodel.config
feemodel.config.history_file = 'data/test.db'
feemodel.config.datadir = 'data/'
from testproxy import proxy, TestMempool
import feemodel.util
feemodel.util.proxy = proxy
import feemodel.app.pools
feemodel.app.pools.minblocks = 5
from feemodel.app.pools import PoolsEstimatorOnline as PEO
from feemodel.app.steadystate import SteadyStateOnline
from feemodel.app.transient import TransientOnline


logging.basicConfig(level=logging.DEBUG)

class SimTest(unittest.TestCase):
    def setUp(self):
        self.mempool = TestMempool()
        self.peo = PEO(25)

    def test_A(self):
        maxtime = 10
        self.ssonline = SteadyStateOnline(self.peo, 25, miniters=0, maxtime=maxtime)
        self.trans = TransientOnline(self.mempool, self.peo, 25, miniters=0, maxtime=maxtime)
        with self.peo.thread_start():
            with self.ssonline.thread_start(), self.trans.thread_start():
                while not (self.ssonline.stats and self.trans.stats):
                    sleep(1)
                print("Finished stats calc.")
                ss_stats = self.ssonline.stats
                trans_stats = self.trans.stats
                ss_stats.print_stats()
                trans_stats.print_stats()
                self.peo.pe.print_pools()
                print("Predicts:")
                print("%d\t%.2f" % (4000, trans_stats.predict(4000)))
                print("%d\t%.2f" % (10000, trans_stats.predict(10000)))
                trans_stats.predictwaits.print_fn()
                print("Inv avg:")
                print("%d\t%.2f" % (2000, trans_stats.avgwaits.inv(2000)))
                print("%d\t%.2f" % (3000, trans_stats.avgwaits.inv(3000)))
                trans_stats.avgwaits.print_fn()

                self.assertIsNone(trans_stats.predict(2999))
                self.assertIsNotNone(trans_stats.predict(3000))
                self.assertEqual(trans_stats.predict(44600), trans_stats.predict(45000))

                minwait = trans_stats.avgwaits.waits[-1]
                self.assertIsNotNone(trans_stats.avgwaits.inv(minwait))
                self.assertIsNone(trans_stats.avgwaits.inv(minwait-1))
                maxwait = trans_stats.avgwaits.waits[0]
                self.assertEqual(trans_stats.avgwaits.inv(maxwait),
                                 trans_stats.avgwaits.inv(maxwait+1))

                self.assertLess(abs(maxtime-ss_stats.timespent), 0.1)
                self.assertLess(abs(maxtime-trans_stats.timespent), 0.1)

    def test_B(self):
        '''test loading of saved stats'''
        self.ssonline = SteadyStateOnline(self.peo, 25, maxtime=10)
        self.assertTrue(self.ssonline.stats)
        self.assertTrue(self.peo.pe)

        if os.path.exists(self.ssonline.savedir):
            shutil.rmtree(self.ssonline.savedir)

    def test_C(self):
        '''Test that miniters and maxiters are enforced.'''
        miniters = 100
        self.ssonline = SteadyStateOnline(self.peo, 25, miniters=miniters, maxtime=0)
        self.trans = TransientOnline(self.mempool, self.peo, 25, miniters=miniters, maxtime=0)

        with self.peo.thread_start():
            with self.ssonline.thread_start(), self.trans.thread_start():
                while not (self.ssonline.stats and self.trans.stats):
                    sleep(1)
                ss_stats = self.ssonline.stats
                trans_stats = self.trans.stats
                self.assertEqual(ss_stats.numiters, miniters)
                self.assertEqual(trans_stats.numiters, miniters)

        if os.path.exists(self.ssonline.savedir):
            shutil.rmtree(self.ssonline.savedir)

        maxiters = 100
        self.peo = PEO(25)
        self.ssonline = SteadyStateOnline(self.peo, 25, miniters=0, maxiters=maxiters)
        self.trans = TransientOnline(self.mempool, self.peo, 25, miniters=0, maxiters=maxiters)

        with self.peo.thread_start():
            with self.ssonline.thread_start(), self.trans.thread_start():
                while not (self.ssonline.stats and self.trans.stats):
                    sleep(1)
                ss_stats = self.ssonline.stats
                trans_stats = self.trans.stats
                self.assertEqual(ss_stats.numiters, maxiters)
                self.assertEqual(trans_stats.numiters, maxiters)

        if os.path.exists(self.peo.savedir):
            shutil.rmtree(self.peo.savedir)
        if os.path.exists(self.ssonline.savedir):
            shutil.rmtree(self.ssonline.savedir)

if __name__ == '__main__':
        unittest.main()
