import unittest
import threading
import logging
from time import sleep
from feemodel.estimate import RectEstimator, ExpEstimator
from feemodel.tests.config import memblock_dbfile as dbfile

logging.basicConfig(level=logging.DEBUG)

feerates = range(0, 100000, 10000)
blockrange = (333931, 333954)


class RectEstimatorTest(unittest.TestCase):
    def test_basic(self):
        tr = RectEstimator(maxsamplesize=10000)
        tr.start(blockrange, dbfile=dbfile)
        print(tr)
        _dum, byterates = tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *tr.calc_mean_byterate()))

    def test_autofeerate(self):
        print("Testing autofeerate:")
        tr = RectEstimator(maxsamplesize=10000)
        tr.start(blockrange, dbfile=dbfile)
        print(tr)
        feerates, byterates = tr.get_byterates()
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *tr.calc_mean_byterate()))

    def test_limit_sample(self):
        maxsamplesize = 1000
        tr = RectEstimator(maxsamplesize=maxsamplesize)
        tr.start(blockrange, dbfile=dbfile)
        print(tr)
        _dum, byterates = tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *tr.calc_mean_byterate()))

    def test_stop(self):
        stopflag = threading.Event()
        tr = RectEstimator(maxsamplesize=1000)
        stopthread = threading.Thread(target=delayed_stop,
                                      args=(stopflag, 0.01))
        stopthread.start()
        self.assertRaises(StopIteration, tr.start, blockrange,
                          stopflag=stopflag, dbfile=dbfile)
        stopthread.join()


class ExpEstimatorTest(unittest.TestCase):
    def test_basic(self):
        print("Starting ExpEstimator test")
        tr = ExpEstimator(3600)
        tr.start(blockrange[1]-1, dbfile=dbfile)
        print(tr)
        print("len(_txsample) is %d" % len(tr._txsample))
        _dum, byterates = tr.get_byterates(feerates)
        for feerate, byterate in zip(feerates, byterates):
            print('%d\t%.2f' % (feerate, byterate))
        print("Mean byterate (error): {}, {:.2f}".format(
            *tr.calc_mean_byterate()))


class SamplingTest(unittest.TestCase):
    '''Generate and re-estimate.'''
    pass


def delayed_stop(stopflag, delay):
    sleep(delay)
    stopflag.set()


if __name__ == '__main__':
    unittest.main()
