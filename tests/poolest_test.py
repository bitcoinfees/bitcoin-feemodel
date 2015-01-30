import logging
import unittest
from feemodel.simul import SimPools
from feemodel.estimate import PoolsEstimator

logging.basicConfig(level=logging.DEBUG)
dbfile = 'data/test.db'

class PoolEstimateTest(unittest.TestCase):
    def test_basic(self):
        self.pe = PoolsEstimator()
        self.pe.start((333931,333953), dbfile=dbfile)
        self.pe.print_pools()

        print(self.pe)


if __name__ == '__main__':
    unittest.main()
