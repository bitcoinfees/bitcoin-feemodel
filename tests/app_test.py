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
        self.ssonline = SteadyStateOnline(self.peo, 25, maxtime=10)
        self.trans = TransientOnline(self.mempool, self.peo,25, maxtime=10)
        with self.peo.thread_start():
            sleep(5)
            self.peo.pe.print_pools()
            with self.ssonline.thread_start(), self.trans.thread_start():
                sleep(20)
                stats = self.ssonline.stats
                stats.qstats.print_stats()
                stats.cap.print_caps()
                self.trans.stats.print_stats()

    def test_B(self):
        '''test loading of saved stats'''
        self.ssonline = SteadyStateOnline(self.peo, 25, maxtime=10)
        self.assertTrue(self.ssonline.stats.height)
        self.assertTrue(self.peo.height)

        if os.path.exists(self.ssonline.savedir):
            shutil.rmtree(self.ssonline.savedir)
        if os.path.exists(self.peo.savedir):
            shutil.rmtree(self.peo.savedir)


if __name__ == '__main__':
        unittest.main()
