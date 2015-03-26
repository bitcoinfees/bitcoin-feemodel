'''Test app.pools.'''
import unittest
import logging
import os
import shutil
import threading
from time import sleep, time
from feemodel.config import datadir
from feemodel.util import save_obj
from feemodel.app.pools import PoolsOnlineEstimator

logging.basicConfig(level=logging.DEBUG)

savedir = os.path.join(datadir, 'pools/.test')
if os.path.exists(savedir):
    shutil.rmtree(savedir)

_timestamp = None


class PoolsOnlineTests(unittest.TestCase):
    def test_A(self):
        print("Test A:")
        poolsonline = PoolsOnlineEstimator(
            2016, 60, dbfile='data/test.db', savedir=savedir)
        t = poolsonline.update_async(333953)
        self.assertIsNotNone(t)
        self.assertFalse(poolsonline)
        sleep(0.5)
        t2 = poolsonline.update_async(333953)
        # Rejected by lock
        self.assertIsNone(t2)
        t.join()
        poolsonline.get_pools().print_pools()
        self.assertEqual(min(poolsonline.get_pools().blockmap.keys()),
                         333931)
        t2 = poolsonline.update_async(333953)
        # Rejected because next_update not yet reached
        self.assertIsNone(t2)
        global _timestamp
        _timestamp = poolsonline.get_pools().timestamp

    def test_B(self):
        print("Test B:")
        # Test loading of saved pools
        # Ensure that the most recent is loaded
        save_obj(1, os.path.join(savedir, 'pe0000000.pickle'))
        update_period = int(time()) - _timestamp + 1
        poolsonline = PoolsOnlineEstimator(
            2016, update_period, dbfile='data/test.db', savedir=savedir)
        self.assertTrue(poolsonline)
        sleep(1)
        stopflag = threading.Event()
        t = poolsonline.update_async(333953, stopflag=stopflag)
        threading.Timer(1, stopflag.set).start()
        # next_update should be reached by now
        self.assertIsNotNone(t)
        poolsonline.poolsestimate.clear_pools()
        # test stopping
        t.join()
        self.assertFalse(poolsonline)

        # test that we keep blockmap even if pools are outdated
        poolsonline = PoolsOnlineEstimator(
            2016, 0, dbfile='data/test.db', savedir=savedir)
        self.assertFalse(poolsonline)
        self.assertTrue(poolsonline.get_pools().blockmap)
        t = poolsonline.update_async(333953)
        self.assertIsNotNone(t)
        t.join()
        self.assertTrue(poolsonline)

        if os.path.exists(savedir):
            shutil.rmtree(savedir)

    def test_C(self):
        print("Test C:")
        # Test small window
        poolsonline = PoolsOnlineEstimator(
            5, 1, dbfile='data/test.db', savedir=savedir)
        t = poolsonline.update_async(333953)
        t.join()
        self.assertEqual(min(poolsonline.get_pools().blockmap.keys()),
                         333949)
        if os.path.exists(savedir):
            shutil.rmtree(savedir)


if __name__ == '__main__':
    unittest.main()
