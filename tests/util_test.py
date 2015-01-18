from feemodel.util import roundRandom, DataSample, interpolate
import unittest
from numpy.random import normal

class RoundRandomTest(unittest.TestCase):
    def test_roundRandom(self):
        std = 0.01
        f = 97.833
        dum, p = divmod(f, 1)
        n = int(p*(1-p)/std**2)
        print("n is %d" % n)
        frand = [roundRandom(f) for i in range(n)]
        self.assertEqual(type(frand[0]), int)
        frandm = sum(frand) / float(len(frand))
        print(frandm)
        diff = abs(f-frandm)
        print("Diff is %.5f" % diff)
        self.assertLess(diff, 1.96*std)


class DataSampleTest(unittest.TestCase):
    def test_dataSample(self):
        sample = normal(size=100000)
        d = DataSample()
#        d.addSample(sample)
        for s in sample:
            d.addSample(s*3 + 1)
        d.calcStats()
        print(d)
        p975 = d.getPercentile(0.975)
        print("97.5th percentile is %f" % p975) # should be 1.96
        p975w = d.getPercentile(0.975, weights = [1]*len(sample))
        self.assertEqual(p975, p975w)
        first = d.getPercentile(1, weights=[1]+ [0]*(len(sample)-1))
        self.assertEqual(first, d.samples[0])


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
