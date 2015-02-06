import unittest
import shutil
import os
import logging
from time import sleep

from testproxy import proxy, TestMempool

import feemodel.config
feemodel.config.history_file = 'data/test.db'
feemodel.config.datadir = 'data/'
import feemodel.util
feemodel.util.proxy = proxy
from feemodel.app.pools import PoolsEstimatorOnline as PEO
from feemodel.app.steadystate import SteadyStateOnline
from feemodel.app.transient import TransientOnline

logging.basicConfig(level=logging.DEBUG)

class SteadyStateTest(unittest.TestCase):
    def setUp(self):
        self.mempool = TestMempool()
        self.peo = PEO(25)

    def test_A(self):
        self.ssonline = SteadyStateOnline(self.peo, 25, miniters=1000, maxtime=10)
        self.trans = TransientOnline(self.mempool, self.peo, 25, maxtime=10)
        with self.peo.thread_start():
            with self.ssonline.thread_start(), self.trans.thread_start():
                while not (self.ssonline.stats and self.trans.stats):
                    sleep(1)
                print("Finished stats calc.")
                self.ssonline.stats.print_stats()
                self.trans.stats.print_stats()
                self.peo.pe.print_pools()
                stats = self.trans.stats
                print("Predicts:")
                print("%d\t%.2f" % (4000, stats.predict(4000)))
                print("%d\t%.2f" % (10000, stats.predict(10000)))
                stats.predictwaits.print_fn()
                print("Inv avg:")
                print("%d\t%.2f" % (2000, stats.avgwaits.inv(2000)))
                print("%d\t%.2f" % (3000, stats.avgwaits.inv(3000)))
                stats.avgwaits.print_fn()

                self.assertIsNone(stats.predict(2999))
                self.assertIsNotNone(stats.predict(3000))
                self.assertEqual(stats.predict(44600), stats.predict(45000))

                minwait = stats.avgwaits.waits[-1]
                self.assertIsNotNone(stats.avgwaits.inv(minwait))
                self.assertIsNone(stats.avgwaits.inv(minwait-1))
                maxwait = stats.avgwaits.waits[0]
                self.assertEqual(stats.avgwaits.inv(maxwait), stats.avgwaits.inv(maxwait+1))


    def test_B(self):
        '''test loading of saved stats'''
        self.ssonline = SteadyStateOnline(self.peo, 25, maxtime=10)
        self.assertTrue(self.ssonline.stats)
        self.assertTrue(self.peo.pe)

        if os.path.exists(self.ssonline.savedir):
            shutil.rmtree(self.ssonline.savedir)
        if os.path.exists(self.peo.savedir):
            shutil.rmtree(self.peo.savedir)


if __name__ == '__main__':
        unittest.main()
