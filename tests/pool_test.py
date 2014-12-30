import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.pools import PoolEstimator
import feemodel.pools
from testconfig import dbFile
from operator import add

savePoolsFile = 'data/savePools.pickle'

class PoolEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.pe = PoolEstimator(savePoolsFile)
        feemodel.pools.poolBlocksWindow = 2016

    def test_pool(self):
        self.pe.identifyPoolBlocks((333931,333953))
        self.pe.estimatePools()
        self.assertEqual(self.pe.poolsCache, self.pe.pools)
        self.pe.saveObject()
        pe2 = PoolEstimator.loadObject(savePoolsFile)
        self.assertEqual(self.pe,pe2)

        os.remove(savePoolsFile)

    def test_poolClears(self):
        feemodel.pools.poolBlocksWindow = 5
        self.pe.identifyPoolBlocks((333931,333953))
        self.pe.estimatePools()
        heights = reduce(add, [list(pool.blockHeights) for pool in self.pe.poolsCache.values()], [])
        self.assertEqual(len(heights), 5)
        self.assertEqual(self.pe.poolsCache, self.pe.pools)
        self.pe.saveObject()
        pe2 = PoolEstimator.loadObject(savePoolsFile)
        self.assertEqual(self.pe,pe2)

        os.remove(savePoolsFile)



if __name__ == '__main__':
    unittest.main()
