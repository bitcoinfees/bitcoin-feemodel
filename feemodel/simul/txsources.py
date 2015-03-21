from __future__ import division

from math import sqrt, cos, exp, log, pi
from random import random
from bisect import bisect, bisect_left
from itertools import groupby

from feemodel.util import DataSample


class SimTx(object):
    def __init__(self, feerate, size):
        self.feerate = feerate
        self.size = size

    def __repr__(self):
        return "SimTx{feerate: %d, size: %d}" % (self.feerate, self.size)


class SimEntry(object):
    def __init__(self, txid, simtx, depends=None):
        self.txid = txid
        self.tx = simtx
        if isinstance(depends, SimDepends):
            self.depends = depends
        else:
            self.depends = SimDepends(depends)

    @classmethod
    def from_mementry(cls, txid, entry):
        return cls(txid, SimTx(entry.feerate, entry.size),
                   depends=entry.depends)

    def __repr__(self):
        return "SimEntry({}, {}, {})".format(
            self.txid, repr(self.tx), repr(self.depends))


class SimDepends(object):
    def __init__(self, depends):
        self._depends = depends if depends else []
        self._depends_bak = depends[:]

    def remove(self, dependency):
        self._depends.remove(dependency)
        return bool(self._depends)

    def reset(self):
        self._depends = self._depends_bak[:]

    def repr(self):
        return "SimDepends({})".format(self._depends)

    def __iter__(self):
        return iter(self._depends)

    def __nonzero__(self):
        return bool(self._depends)


class SimTxSource(object):
    def __init__(self, txsample, txrate):
        self._txsample = [(simtx.feerate, simtx.size, '')
                          for simtx in txsample]
        self.txrate = txrate

    def generate_txs(self, time_interval):
        if not self._txsample:
            raise ValueError("No txs.")
        k = poisson_sample(self.txrate*time_interval)
        n = len(self._txsample)

        return [self._txsample[int(random()*n)] for i in range(k)]

    def get_txsample(self):
        return [SimTx(tx[0], tx[1]) for tx in self._txsample]

    def get_byterates(self, feerates=None):
        '''Get reverse cumulative byterate as a function of feerate.'''
        if not self._txsample:
            raise ValueError("No txs.")
        n = len(self._txsample)
        if feerates:
            # feerates assumed sorted.
            binnedrates = [0.]*len(feerates)
            for tx in self._txsample:
                fidx = bisect(feerates, tx[0])
                if fidx:
                    binnedrates[fidx-1] += tx[1]
            # for entry in self._txsample:
            #     fidx = bisect(feerates, entry.tx.feerate)
            #     if fidx:
            #         binnedrates[fidx-1] += entry.tx.size
            byterates = [sum(binnedrates[idx:])*self.txrate/n
                         for idx in range(len(binnedrates))]
            return feerates, byterates
        else:
            # Choose the feerates so that the byterate in each interval
            # is ~ 0.1 of the total.
            R = 10  # 1 / 0.1
            txrate = self.txrate
            self._txsample.sort(reverse=True)
            feerates = []
            byterates = []
            cumbyterate = 0.
            for feerate, feegroup in groupby(self._txsample,
                                             lambda tx: tx[0]):
                cumbyterate += sum([tx[1] for tx in feegroup])*txrate/n
                feerates.append(feerate)
                byterates.append(cumbyterate)

            totalbyterate = byterates[-1]
            byteratetargets = [i/R*totalbyterate for i in range(1, R+1)]
            feerates_bin = []
            byterates_bin = []
            for target in byteratetargets:
                idx = bisect_left(byterates, target)
                feerates_bin.append(feerates[idx])
                byterates_bin.append(byterates[idx])

            feerates_bin.reverse()
            byterates_bin.reverse()
            return feerates_bin, byterates_bin

            # txs = [entry.tx for entry in self._txsample]
            # byteratesmap = defaultdict(float)
            # for tx in txs:
            #     byteratesmap[tx.feerate] += tx.size*txrate/n
            # byterates = sorted(byteratesmap.items(), reverse=True)
            # cumbyterates = []
            # cumbyterate = 0.
            # for byterate in byterates:
            #     cumbyterate += byterate[1]
            #     cumbyterates.append((byterate[0], cumbyterate))
            # feerates, byterates = zip(*cumbyterates)
            # totalbyterate = byterates[-1]
            # byteratetargets = [i/R*totalbyterate for i in range(1, R+1)]
            # feerates_bin = []
            # byterates_bin = []
            # for target in byteratetargets:
            #     idx = bisect_left(byterates, target)
            #     feerates_bin.append(feerates[idx])
            #     byterates_bin.append(byterates[idx])

            # feerates_bin.reverse()
            # byterates_bin.reverse()
            # return feerates_bin, byterates_bin

    def calc_mean_byterate(self):
        '''Calculate the mean byterate.

        Returns the mean byterate with its standard error, computed using
        bootstrap resampling.
        '''
        n = len(self._txsample)

        def _calc_single(_txsample):
            return sum([tx[1] for tx in _txsample])*self.txrate/n
            # return sum([entry.tx.size for entry in _txsample])*self.txrate/n

        mean_byterate = _calc_single(self._txsample)
        bootstrap_ests = DataSample()
        for i in range(1000):
            txsample = [self._txsample[int(random()*n)] for idx in range(n)]
            bootstrap_ests.add_datapoints([_calc_single(txsample)])

        bootstrap_ests.calc_stats()
        std = bootstrap_ests.std

        return mean_byterate, std

    def __repr__(self):
        return "SimTxSource{{samplesize: {}, txrate: {}}}".format(
            len(self._txsample), self.txrate)


def poisson_sample(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution
    # #Generating_Poisson-distributed_random_variables
    if l > 30:
        return int(round(poisson_approx(l)))
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return k - 1


def poisson_approx(l):
    # box-muller
    u = random()
    v = random()

    z = sqrt(-2*log(u))*cos(2*pi*v)
    return z*sqrt(l) + l
