'''Test app.pools.'''
from __future__ import division

import unittest
import threading
from time import sleep, time

from feemodel.tests.config import mk_tmpdatadir, rm_tmpdatadir, poolsref
from feemodel.tests.pseudoproxy import install

from feemodel.util import save_obj, get_hashesperblock
from feemodel.app.pools import PoolsOnlineEstimator, SAVEFILE as pools_savefile

install()


class PoolsOnlineTests(unittest.TestCase):

    def setUp(self):
        self.datadir = mk_tmpdatadir()

    def test_A(self):
        print("Test A:")
        poolsonline = PoolsOnlineEstimator(2016, update_period=3600,
                                           minblocks=1)
        t = poolsonline.update_async(333953)
        self.assertIsNotNone(t)
        self.assertFalse(poolsonline)
        sleep(0.5)
        t2 = poolsonline.update_async(333953)
        # Rejected by lock
        self.assertIsNone(t2)
        t.join()
        pe = poolsonline.get_pools()
        pe.print_pools()
        print(poolsonline.get_stats())

        self.assertEqual(
            min(pe.blocksmetadata.keys()), 333931)
        t2 = poolsonline.update_async(333953)
        # Rejected because next_update not yet reached
        self.assertIsNone(t2)

    def test_B(self):
        print("Test B:")
        # Test loading of saved pools
        poolsref.timestamp = time()
        save_obj(poolsref, pools_savefile)
        poolsonline = PoolsOnlineEstimator(2016, update_period=1,
                                           minblocks=1)
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

        # test that we keep blocksmetadata even if pools are outdated
        poolsonline = PoolsOnlineEstimator(2016, update_period=0,
                                           minblocks=1)
        self.assertFalse(poolsonline)
        self.assertTrue(poolsonline.get_pools().blocksmetadata)
        t = poolsonline.update_async(333953)
        self.assertIsNotNone(t)
        t.join()
        self.assertTrue(poolsonline)

    def test_C(self):
        print("Test C:")
        poolsref.timestamp = time() - 1
        save_obj(poolsref, pools_savefile)
        # Test small window
        poolsonline = PoolsOnlineEstimator(5, update_period=0, minblocks=1)
        self.assertFalse(poolsonline)
        self.assertTrue(poolsonline.get_pools().blocksmetadata)
        t = poolsonline.update_async(333953)
        t.join()
        self.assertEqual(min(poolsonline.get_pools().blocksmetadata.keys()),
                         333949)

    def test_D(self):
        print("Test D:")
        # Test that blockrate is updated at retarget boundaries
        poolsonline = PoolsOnlineEstimator(
            2016,
            update_period=3600,
            minblocks=1)
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

    def tearDown(self):
        rm_tmpdatadir()


if __name__ == '__main__':
    unittest.main()
