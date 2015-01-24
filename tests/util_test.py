import unittest
from numpy.random import normal

from feemodel.util import get_coinbase_info
from feemodel.util import round_random, DataSample, interpolate


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
        frandm = sum(frand) / float(len(frand)) # The sample mean
        print(frandm)
        diff = abs(f-frandm)
        print("Diff is %.5f" % diff)
        self.assertLess(diff, 1.96*target_std) # This should be true with 95% probability


class DataSampleTest(unittest.TestCase):
    def test_dataSample(self):
        sample = normal(size=100000)
        s = 3
        m = 1
        d = DataSample()
        for dp in sample:
            d.add_datapoints([dp*s + m])
        d.calc_stats()
        print(d)
        p975 = d.get_percentile(0.975)
        print("97.5th percentile is %f, should be %.2f." % (p975, 1.96*s + m))
        p975w = d.get_percentile(0.975, weights = [1]*len(sample))
        self.assertEqual(p975, p975w)
        first = d.get_percentile(1, weights=[1]+ [0]*(len(sample)-1))
        self.assertEqual(first, d.datapoints[0])


class InterpolateTest(unittest.TestCase):
    def test_interpolate(self):
        x = [1.5, 3.5]
        y = [10.0, 5.0]

        X = [1.9, 4, 0, 1.5, 3.5]
        Y = [9, 5.0, 10.0, 10, 5]
        for x0, y0ref in zip(X,Y):
            y0, dum = interpolate(x0, x, y)
            self.assertEqual(y0, y0ref)


if __name__ == '__main__':
    unittest.main()
