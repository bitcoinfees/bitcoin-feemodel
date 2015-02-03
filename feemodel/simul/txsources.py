from math import sqrt, cos, exp, log, pi
from bisect import bisect
from random import random
from copy import copy
from feemodel.util import DataSample


class SimTx(object):
    def __init__(self, size, feerate, _id='', _depends=None):
        self.size = size
        self.feerate = feerate
        if _depends is None:
            _depends = []
        self._depends = _depends
        self._id = _id
        if not _id:
            assert not _depends

    @classmethod
    def from_mementry(cls, txid, entry):
        return cls(entry.size, entry.feerate, _id=txid, _depends=entry.depends)

    def __copy__(self):
        return SimTx(self.size, self.feerate, self._id, self._depends[:])

    def __cmp__(self, other):
        return cmp(self.feerate, other.feerate)

    def __repr__(self):
        return "SimTx{size: %d, feerate: %d}" % (
            self.size, self.feerate)


class SimTxSource(object):
    def __init__(self, txsample, txrate):
        self.txsample = txsample
        self.txrate = txrate

    def generate_txs(self, time_interval):
        if not self.txsample:
            raise ValueError("Empty txsample.")
        k = poisson_sample(self.txrate*time_interval)
        n = len(self.txsample)

        return [self.txsample[int(random()*n)] for i in range(k)]

    def get_byterates(self, feerates):
        '''Get byterates as a function of feerate.'''
        # feerates assumed sorted.
        n = float(len(self.txsample))
        byterates = [0.]*len(feerates)
        for tx in self.txsample:
            fee_idx = bisect(feerates, tx.feerate)
            if fee_idx > 0:
                byterates[fee_idx-1] += self.txrate*tx.size/n
        return byterates

    def calc_mean_byterate(self):
        '''Calculate the mean byterate.

        Returns the mean byterate with its standard error, computed using
        bootstrap resampling.
        '''
        n = len(self.txsample)

        def _calc_single(txsample):
            return sum([tx.size for tx in txsample])*self.txrate/float(n)

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


class TxSourceCopy(SimTxSource):
    # This is so slow :(
    def generate_txs(self, time_interval):
        k = poisson_sample(self.txrate*time_interval)
        n = len(self.txsample)

        return [copy(self.txsample[int(random()*n)]) for i in range(k)]


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
