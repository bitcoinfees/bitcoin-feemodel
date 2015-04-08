from __future__ import division

from libc.stdlib cimport rand, srand, RAND_MAX
from libc.time cimport time
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)

from math import sqrt, cos, exp, log, pi
from random import random, normalvariate
from bisect import bisect, bisect_left
from itertools import groupby

from tabulate import tabulate

from feemodel.util import DataSample

DEF OVERALLOCATE = 1.25  # This better be > 1.
assert OVERALLOCATE > 1

srand(time(NULL))


class SimTx(object):

    def __init__(self, feerate, size):
        self.feerate = feerate
        self.size = size

    def __repr__(self):
        return "SimTx(feerate: %d, size: %d)" % (self.feerate, self.size)


class SimTxSource(object):

    def __init__(self, txsample, txrate):
        self._txsample = [(simtx.feerate, simtx.size, '')
                          for simtx in txsample]
        self.txrate = txrate

    def get_txgen(self, feeratethresh=0):
        '''Python wrapper for get_c_txgen.'''
        c_txgen = self.get_c_txgen(feeratethresh)
        txs = TxPtrArray()

        def txgen(time_interval):
            newtxs = []
            txs.clear()
            c_txgen(txs, time_interval)
            for i in range(txs.size):
                newtxs.append(SimTx(txs.txs[i].feerate, txs.txs[i].size))
            return newtxs

        return txgen

    def get_c_txgen(self, feeratethresh=0):
        # TODO: test the feerate thresh / stable feerate
        if not self._txsample:
            raise ValueError("No txs.")
        txsample_array = TxSampleArray([
            SimTx(tx[0], tx[1]) for tx in self._txsample
            if tx[0] >= feeratethresh])
        modtxrate = len(txsample_array) / len(self._txsample) * self.txrate

        def txgen(TxPtrArray txs, time_interval):
            '''Put the new samples in txs.'''
            numtxs = poisson_sample(modtxrate*time_interval)
            txsample_array.sample(txs, numtxs)

        return txgen

    def get_txsample(self):
        return [SimTx(tx[0], tx[1]) for tx in self._txsample]

    def get_byterates(self, feerates=None):
        '''Get reverse cumulative byterate as a function of feerate.'''
        if not self._txsample:
            raise ValueError("No txs.")
        n = len(self._txsample)
        if feerates:
            feerates.sort()
            ratebins = [0.]*len(feerates)
            for txfeerate, txsize, _dum in self._txsample:
                fidx = bisect(feerates, txfeerate)
                if fidx:
                    ratebins[fidx-1] += txsize
            byterates = [sum(ratebins[idx:])*self.txrate/n
                         for idx in range(len(ratebins))]
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
            # TODO: remove duplicate feerates
            return feerates_bin, byterates_bin

    def calc_mean_byterate(self):
        '''Calculate the mean byterate.

        Returns the mean byterate with its standard error, computed using a
        normal approximation.
        '''
        d = DataSample([tx[1]*self.txrate for tx in self._txsample])
        d.calc_stats()
        return d.mean, d.std / len(self._txsample)**0.5

    def print_rates(self):
        if not self:
            print("No txsample.")
        feerates, byterates = self.get_byterates()
        headers = ['Feerate', 'Cumulative byterate']
        table = zip(feerates, byterates)
        print(tabulate(table, headers=headers))
        mean_byterate, std = self.calc_mean_byterate()
        print("Mean byterate (std): {} ({})".format(mean_byterate, std))

    def __repr__(self):
        return "SimTxSource(samplesize: {}, txrate: {})".format(
            len(self._txsample), self.txrate)

    def __nonzero__(self):
        return bool(len(self._txsample))


cdef class TxSampleArray:

    def __cinit__(self, txsample):
        self.size = len(txsample)
        self.txsample = <TxStruct *>malloc(self.size*sizeof(TxStruct))
        for idx, tx in enumerate(txsample):
            self.txsample[idx].feerate = tx.feerate
            self.txsample[idx].size = tx.size
            self.txsample[idx].txid = NULL
        self._randlimit = RAND_MAX - (RAND_MAX % self.size)

    cdef void sample(self, TxPtrArray txs, int l):
        cdef int newsize
        newsize = txs.size + l
        if newsize >= txs.maxsize:
            txs._resize(<int>((newsize+1)*OVERALLOCATE))
        for idx in range(l):
            ridx = randindex(self.size, self._randlimit)
            txs.append(&self.txsample[ridx])

    def __len__(self):
        return self.size

    def __dealloc__(self):
        free(self.txsample)


cdef class TxPtrArray:

    def __cinit__(self, int maxsize=0):
        self.size = 0
        self._resize(maxsize)

    cdef void append(self, TxStruct *tx):
        '''Append to the array.'''
        if self.size == self.maxsize:
            self._resize(<int>((self.size+1)*OVERALLOCATE))
        self.txs[self.size] = tx
        self.size += 1

    cdef void extend(self, TxStruct **txs, int size):
        '''Extend the array.'''
        cdef int newsize
        newsize = self.size + size
        if newsize >= self.maxsize:
            self._resize(<int>((newsize+1)*OVERALLOCATE))
        for idx in range(size):
            self.txs[self.size] = txs[idx]
            self.size += 1

    cdef TxStruct* pop(self):
        '''Pop the last element.

        Do not do any resizing.
        '''
        if self.size == 0:
            return NULL
        self.size -= 1
        return self.txs[self.size]

    cdef void clear(self):
        '''Clear the array..'''
        self._resize(0)
        self.size = 0

    cdef void _resize(self, int newmaxsize):
        self.txs = <TxStruct **>realloc(self.txs, newmaxsize*sizeof(TxStruct *))
        self.maxsize = newmaxsize

    cdef txs_copy(self, TxPtrArray other):
        '''Copy txs from self to other.'''
        if other.maxsize <= self.size:
            other._resize(<int>((self.size+1)*OVERALLOCATE))
        other.size = self.size
        for i in range(self.size):
            other.txs[i] = self.txs[i]

    def get_simtxs(self):
        simtxs = [
            SimTx(self.txs[idx].feerate, self.txs[idx].size)
            for idx in range(self.size)
            if self.txs[idx] is not NULL]
        return simtxs

    def __len__(self):
        '''Array size.'''
        return self.size

    def __dealloc__(self):
        free(self.txs)


cdef int randindex(int N, int randlimit):
    '''Get a random index in the range [0, N-1].'''
    cdef int r
    r = RAND_MAX
    # randlimit is always RAND_MAX - (RAND_MAX % N); we however pass it as an
    # argument to avoid having to recompute it every time. Its purpose is to
    # ensure the resulting R.V. has a uniform distribution over
    # {0, 1, ..., N-1}.
    while r >= randlimit:
        r = rand()
    return r % N


cdef poisson_sample(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution
    # #Generating_Poisson-distributed_random_variables
    cdef:
        float p
        int k
        float L

    if l > 30:
        return poisson_approx(l)
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return int(k - 1)


cdef poisson_approx(l):
    '''Normal approximation.'''
    return int(normalvariate(l, l**0.5))
