import unittest
import threading
import logging
from time import sleep
from feemodel.estimate import RectEstimator, ExpEstimator

logging.basicConfig(level=logging.DEBUG)
dbfile = 'data/test.db'

feerates = range(0, 100000, 10000)
blockrange = (333931, 333954)


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


class RectEstimatorTest(unittest.TestCase):
    def test_basic(self):
        self.tr = RectEstimator(maxsamplesize=10000)
        self.tr.start(blockrange, dbfile=dbfile)
        print(self.tr)
        # num_uniquetxs = len(set(self.tr._txsample))
        # self.assertEqual(num_uniquetxs, len(self.tr._txsample))
        _dum, byterates = self.tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *self.tr.calc_mean_byterate()))

    def test_autofeerate(self):
        print("Testing autofeerate:")
        self.tr = RectEstimator(maxsamplesize=10000)
        self.tr.start(blockrange, dbfile=dbfile)
        print(self.tr)
        # num_uniquetxs = len(set(self.tr._txsample))
        # self.assertEqual(num_uniquetxs, len(self.tr._txsample))
        feerates, byterates = self.tr.get_byterates()
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *self.tr.calc_mean_byterate()))

    def test_limit_sample(self):
        maxsamplesize = 1000
        self.tr = RectEstimator(maxsamplesize=maxsamplesize)
        self.tr.start(blockrange, dbfile=dbfile)
        print(self.tr)
        # num_uniquetxs = len(set(self.tr._txsample))
        # self.assertEqual(num_uniquetxs, len(self.tr._txsample))
        # self.assertEqual(num_uniquetxs, maxsamplesize)
        _dum, byterates = self.tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *self.tr.calc_mean_byterate()))

    def test_stop(self):
        stopflag = threading.Event()
        self.tr = RectEstimator(maxsamplesize=1000)
        stopthread = threading.Thread(target=delayed_stop, args=(stopflag, 0.01))
        stopthread.start()
        self.assertRaises(StopIteration, self.tr.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()


class ExpEstimatorTest(unittest.TestCase):
    def test_basic(self):
        print("Starting ExpEstimator test")
        self.tr = ExpEstimator(3600)
        self.tr.start(blockrange[1]-1, dbfile=dbfile)
        print(self.tr)
        print("len(_txsample) is %d" % len(self.tr._txsample))
        _dum, byterates = self.tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *self.tr.calc_mean_byterate()))


class SamplingTest(unittest.TestCase):
    '''Test whether
    '''
    pass


if __name__ == '__main__':
    unittest.main()
