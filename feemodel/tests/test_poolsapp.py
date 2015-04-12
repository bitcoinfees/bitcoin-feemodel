'''Test app.pools.'''
from __future__ import division

import unittest
import logging
import os
import shutil
import threading
from time import sleep, time
from random import seed

from feemodel.tests.pseudoproxy import install

from feemodel.config import datadir
from feemodel.util import save_obj, get_hashesperblock
from feemodel.app.pools import PoolsOnlineEstimator

from feemodel.tests.config import memblock_dbfile as dbfile

install()
seed(0)
logging.basicConfig(level=logging.DEBUG)

savedir = os.path.join(datadir, 'pools/.test')
if os.path.exists(savedir):
    shutil.rmtree(savedir)

_timestamp = None


class PoolsOnlineTests(unittest.TestCase):
    def test_A(self):
        print("Test A:")
        poolsonline = PoolsOnlineEstimator(
            2016,
            update_period=60,
            minblocks=1,
            dbfile=dbfile,
            savedir=savedir)
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
            2016,
            update_period=update_period,
            minblocks=1,
            dbfile=dbfile,
            savedir=savedir)
        self.assertTrue(poolsonline)
        sleep(1)
        stopflag = threading.Event()
        t = poolsonline.update_async(333953, stopflag=stopflag)
        threading.Timer(1, stopflag.set).start()
        # next_update should be reached by now
        self.assertIsNotNone(t)
        poolsonline.poolsestimate.pools = {}
        # test stopping
        t.join()
        self.assertFalse(poolsonline)

        # test that we keep blockmap even if pools are outdated
        poolsonline = PoolsOnlineEstimator(
            2016,
            update_period=0,
            minblocks=1,
            dbfile=dbfile,
            savedir=savedir)
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
            5,
            update_period=1,
            minblocks=1,
            dbfile=dbfile,
            savedir=savedir)
        t = poolsonline.update_async(333953)
        t.join()
        self.assertEqual(min(poolsonline.get_pools().blockmap.keys()),
                         333949)
        if os.path.exists(savedir):
            shutil.rmtree(savedir)

    def test_D(self):
        print("Test D:")
        # Test that blockrate is updated at retarget boundaries
        poolsonline = PoolsOnlineEstimator(
            2016,
            update_period=60,
            minblocks=1,
            dbfile=dbfile,
            savedir=savedir)
        t = poolsonline.update_async(333953)
        t.join()
        blockrate_ref = poolsonline.get_pools().blockrate
        print("The ref blockrate is {}".format(blockrate_ref))
        t = poolsonline.update_async(334655)
        self.assertIsNone(t)
        sleep(0.1)
        self.assertEqual(blockrate_ref, poolsonline.get_pools().blockrate)
        t = poolsonline.update_async(334656)
        sleep(0.1)
        blockrate_new = poolsonline.get_pools().blockrate
        self.assertNotEqual(blockrate_ref, blockrate_new)
        print("The new blockrate is {}".format(blockrate_new))
        ref_hashesperblock = get_hashesperblock(334655)
        new_hashesperblock = get_hashesperblock(334656)
        self.assertEqual(new_hashesperblock/ref_hashesperblock,
                         blockrate_ref/blockrate_new)
        print("Difficulty should not be recalculated:")
        t = poolsonline.update_async(334657)
        self.assertIsNone(t)
        sleep(0.1)


if __name__ == '__main__':
    unittest.main()
