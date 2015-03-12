from __future__ import division

from math import sqrt, cos, exp, log, pi
from random import random
from bisect import bisect, bisect_left
from collections import defaultdict

from feemodel.util import DataSample


class SimTx(object):
    def __init__(self, size, feerate):
        self.size = size
        self.feerate = feerate

    def __repr__(self):
        return "SimTx{size: %d, feerate: %d}" % (
            self.size, self.feerate)


class SimEntry(object):
    def __init__(self, txid, simtx, depends=None):
        self._id = txid
        self.tx = simtx
        self.depends = depends if depends else []
        self._depends_bak = self.depends[:]

    @classmethod
    def from_mementry(cls, txid, mementry):
        simtx = SimTx(mementry.size, mementry.feerate)
        return cls(txid, simtx, depends=mementry.depends)

    def _reset_deps(self):
        self.depends = self._depends_bak[:]

    def __cmp__(self, other):
        # This is used by bisect.insort in SimMempool._process_block
        return cmp(self.tx.feerate, other.tx.feerate)


class SimTxSource(object):
    def __init__(self, txsample, txrate):
        self.txsample = [SimEntry('', simtx) for simtx in txsample]
        self.txrate = txrate

    def generate_txs(self, time_interval):
        if not self.txsample:
            raise ValueError("No txs.")
        k = poisson_sample(self.txrate*time_interval)
        n = len(self.txsample)

        return [self.txsample[int(random()*n)] for i in range(k)]

    def get_byterates(self, feerates=None):
        '''Get reverse cumulative byterate as a function of feerate.'''
        if not self.txsample:
            raise ValueError("No txs.")
        n = len(self.txsample)
        if feerates:
            # feerates assumed sorted.
            binnedrates = [0.]*len(feerates)
            for entry in self.txsample:
                fidx = bisect(feerates, entry.tx.feerate)
                if fidx:
                    binnedrates[fidx-1] += entry.tx.size
            byterates = [sum(binnedrates[idx:])*self.txrate/n
                         for idx in range(len(binnedrates))]
            return feerates, byterates
        else:
            # Choose the feerates so that the byterate in each interval
            # is ~ 0.1 of the total.
            R = 10  # 1 / 0.1
            txrate = self.txrate
            txs = [entry.tx for entry in self.txsample]
            byteratesmap = defaultdict(float)
            for tx in txs:
                byteratesmap[tx.feerate] += tx.size*txrate/n
            byterates = sorted(byteratesmap.items(), reverse=True)
            cumbyterates = []
            cumbyterate = 0.
            for byterate in byterates:
                cumbyterate += byterate[1]
                cumbyterates.append((byterate[0], cumbyterate))
            feerates, byterates = zip(*cumbyterates)
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

    def calc_mean_byterate(self):
        '''Calculate the mean byterate.

        Returns the mean byterate with its standard error, computed using
        bootstrap resampling.
        '''
        n = len(self.txsample)

        def _calc_single(txsample):
            return sum([entry.tx.size for entry in txsample])*self.txrate/n

        mean_byterate = _calc_single(self.txsample)
        bootstrap_ests = DataSample()
        for i in range(1000):
            txsample = [self.txsample[int(random()*n)] for idx in range(n)]
            bootstrap_ests.add_datapoints([_calc_single(txsample)])

        bootstrap_ests.calc_stats()
        std = bootstrap_ests.std

        return mean_byterate, std

    def __repr__(self):
        return "SimTxSource{{samplesize: {}, txrate: {}}}".format(
            len(self.txsample), self.txrate)


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
