import unittest
from random import random, seed

from feemodel.util import get_coinbase_info
from feemodel.util import round_random, DataSample, interpolate
from feemodel.util import Function

from feemodel.tests.pseudoproxy import install

# TODO: poisson sampling test for simul.txsources
install()
seed(0)


class GetCoinbaseInfoTest(unittest.TestCase):

    def test_get_coinbase_info(self):
        for height in range(339000, 334000):
            addresses, tag = get_coinbase_info(blockheight=height)

        for height in range(333940, 333950):
            addresses, tag = get_coinbase_info(blockheight=height)
            print("%d:\t%s\t%s" % (height, addresses[0], repr(tag)))


class RoundRandomTest(unittest.TestCase):

    def test_round_random(self):
        target_std = 0.01
        f = 97.833  # Just a random float
        dum, p = divmod(f, 1)
        # Size of sample needed such that the standard deviation of the
        # sample mean is equal to target_std
        n = int(p*(1-p)/target_std**2)
        print("n is %d" % n)
        frand = [round_random(f) for i in range(n)]
        self.assertEqual(type(frand[0]), int)
        frandm = sum(frand) / float(len(frand))  # The sample mean
        print(frandm)
        diff = abs(f-frandm)
        print("Diff is %.5f" % diff)
        # This should be true with 95% probability
        self.assertLess(diff, 1.96*target_std)


class DataSampleTest(unittest.TestCase):

    def test_datasample(self):
        sample = [random() for i in xrange(100000)]
        d = DataSample()
        d.add_datapoints(sample)
        d.calc_stats()
        print(d)
        p975 = d.get_percentile(0.975)
        print("97.5th percentile is %f, should be %f." % (p975, 0.975))
        p975w = d.get_percentile(0.975, weights=[1]*len(sample))
        self.assertEqual(p975, p975w)
        first = d.get_percentile(1, weights=[1] + [0]*(len(sample)-1))
        self.assertEqual(first, d.datapoints[0])


class InterpolateTest(unittest.TestCase):

    def test_interpolate(self):
        x = [1.5, 3.5]
        y = [10.0, 5.0]

        X = [1.9, 4, 0, 1.5, 3.5]
        Y = [9, 5.0, 10.0, 10, 5]
        for x0, y0ref in zip(X, Y):
            y0, dum = interpolate(x0, x, y)
            self.assertEqual(y0, y0ref)


class FunctionTest(unittest.TestCase):

    def test_A(self):
        def fn(x):
            return -2*x
        x = range(10)
        y = map(fn, x)
        f = Function(x, y)
        for i in range(10):
            x0 = random()*9
            self.assertEqual(fn(x0), f(x0))
            y0 = fn(x0)
            self.assertEqual(f.inv(y0), x0)
        self.assertIsNone(f(10))
        self.assertIsNone(f(-1))
        self.assertIsNone(f.inv(-19))
        self.assertIsNone(f.inv(1))

        self.assertEqual(f(10, use_upper=True), f(9))
        self.assertEqual(f(-1, use_lower=True), f(0))
        self.assertEqual(f.inv(-19, use_lower=True), f.inv(-18))
        self.assertEqual(f.inv(1, use_upper=True), f.inv(0))


if __name__ == '__main__':
    unittest.main()
