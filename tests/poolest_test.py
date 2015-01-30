import logging
import unittest
import threading
from time import sleep
from feemodel.simul import SimPools
from feemodel.estimate import PoolsEstimator

logging.basicConfig(level=logging.DEBUG)
dbfile = 'data/test.db'
blockrange = (333931, 333953)


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


class PoolEstimateTest(unittest.TestCase):
    def test_basic(self):
        self.pe = PoolsEstimator()
        self.pe.start(blockrange, dbfile=dbfile)
        self.pe.print_pools()

        print(self.pe)

    def test_stop(self):
        stopflag = threading.Event()
        self.pe = PoolsEstimator()
        stopthread = threading.Thread(target=delayed_stop, args=(stopflag, 1))
        stopthread.start()
        self.assertRaises(StopIteration, self.pe.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()



if __name__ == '__main__':
    unittest.main()
