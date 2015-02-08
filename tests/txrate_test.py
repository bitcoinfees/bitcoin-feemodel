import unittest
import threading
import logging
from time import sleep
from feemodel.estimate import TxRateEstimator

logging.basicConfig(level=logging.DEBUG)
dbfile = 'data/test.db'

feerates = range(0, 100000, 10000)
blockrange = (333931, 333953)


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


class TxRatesEstimatorTest(unittest.TestCase):
    def test_basic(self):
        self.tr = TxRateEstimator(maxsamplesize=10000)
        self.tr.start(blockrange, dbfile=dbfile)
        print(self.tr)
        num_uniquetxs = len(set(self.tr.txsample))
        self.assertEqual(num_uniquetxs, len(self.tr.txsample))
        byterates = self.tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *self.tr.calc_mean_byterate()))

    def test_limit_sample(self):
        maxsamplesize = 1000
        self.tr = TxRateEstimator(maxsamplesize=maxsamplesize)
        self.tr.start(blockrange, dbfile=dbfile)
        print(self.tr)
        num_uniquetxs = len(set(self.tr.txsample))
        self.assertEqual(num_uniquetxs, len(self.tr.txsample))
        self.assertEqual(num_uniquetxs, maxsamplesize)
        byterates = self.tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *self.tr.calc_mean_byterate()))

    def test_stop(self):
        stopflag = threading.Event()
        self.tr = TxRateEstimator(maxsamplesize=1000)
        stopthread = threading.Thread(target=delayed_stop, args=(stopflag, 0.01))
        stopthread.start()
        self.assertRaises(StopIteration, self.tr.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()


class SamplingTest(unittest.TestCase):
    '''Test whether
    '''
    pass


if __name__ == '__main__':
    unittest.main()
