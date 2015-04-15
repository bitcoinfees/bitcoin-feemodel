from __future__ import division

from libc.stdlib cimport rand, srand, RAND_MAX
from libc.time cimport time
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)
from feemodel.simul.simul cimport SimMempool

from math import sqrt, cos, exp, log, pi
from random import random, normalvariate
from bisect import bisect, bisect_left
from itertools import groupby

from tabulate import tabulate

from feemodel.util import DataSample, cumsum_gen

DEF OVERALLOCATE = 2  # This better be > 1.
# TODO: investigate the behaviour of this when multiprocessing.
srand(time(NULL))

DEFAULT_PRINT_FEERATES = range(0, 55000, 5000)


class SimTx(object):

    def __init__(self, feerate, size):
        self.feerate = feerate
        self.size = size

    def __repr__(self):
        return "SimTx(feerate: %d, size: %d)" % (self.feerate, self.size)


class SimTxSource(object):

    def __init__(self, txsample, txrate):
        self.txsample = txsample
        self.txrate = txrate

    def get_emitter(self, SimMempool mempool not None, feeratethresh=0):
        if self.txrate and not self.txsample:
            raise ValueError("Non-zero txrate with empty txsample.")
        txsample_filtered = filter(lambda tx: tx.feerate >= feeratethresh,
                                   self.txsample)
        txsample_array = TxSampleArray(txsample_filtered)
        if self.txrate:
            filtered_txrate = (len(txsample_filtered) / len(self.txsample) *
                               self.txrate)
        else:
            filtered_txrate = 0

        def tx_emitter(time_interval):
            """Emit new txs into mempool.

            Number of new txs is a Poisson R.V. with expected value equal to
            filtered_txrate * time_interval.

            This is called in Simul.run once per simblock; time_interval is
            thus the block interval.
            """
            numtxs = poissonvariate(filtered_txrate*time_interval)
            txsample_array.sample(&mempool.txqueue, numtxs)

        return tx_emitter

    def get_byterates(self, feerates=None):
        '''Get reverse cumulative byterate as a function of feerate.'''
        if not self.txsample:
            raise ValueError("No txs.")
        n = len(self.txsample)

        def feerate_keyfn(simtx):
            return simtx.feerate

        def byterate_groupsum(grouptuple):
            return sum([tx.size for tx in grouptuple[1]])*self.txrate/n

        self.txsample.sort(key=feerate_keyfn, reverse=True)
        _feerates = sorted(set(map(feerate_keyfn, self.txsample)))
        _byterates = list(cumsum_gen(
            groupby(self.txsample, feerate_keyfn), mapfn=byterate_groupsum))
        _byterates.reverse()
        if _feerates[0] != 0:
            _feerates.insert(0, 0)
            _byterates.insert(0, _byterates[0])

        if feerates:
            n = len(_feerates)
            byterates = []
            for feerate in feerates:
                bidx = bisect_left(_feerates, feerate)
                byterates.append(_byterates[bidx] if bidx < n else 0)
        else:
            feerates = _feerates
            byterates = _byterates

        return feerates, byterates

    def calc_mean_byterate(self):
        '''Calculate the mean byterate.

        Returns the mean byterate with its standard error, computed using a
        normal approximation.
        '''
        d = DataSample([tx.size*self.txrate for tx in self.txsample])
        d.calc_stats()
        return d.mean, d.std / len(self.txsample)**0.5

    def print_rates(self, feerates=DEFAULT_PRINT_FEERATES):
        if not self:
            print("No txsample.")
        feerates, byterates = self.get_byterates(feerates=feerates)
        headers = ['Feerate', 'Cumulative byterate']
        table = zip(feerates, byterates)
        print(tabulate(table, headers=headers))
        mean_byterate, std = self.calc_mean_byterate()
        print("Mean byterate (std): {} ({})".format(mean_byterate, std))

    def __repr__(self):
        return "SimTxSource(samplesize: {}, txrate: {})".format(
            len(self.txsample), self.txrate)

    def __nonzero__(self):
        return self.txrate is not None


cdef class TxSampleArray:

    def __cinit__(self, txsample):
        cdef TxStruct tx
        self.txsample = txarray_init(len(txsample))
        for idx, simtx in enumerate(txsample):
            tx.feerate = simtx.feerate
            tx.size = simtx.size
            txarray_append(&self.txsample, tx)
        if self.txsample.size:
            self._randlimit = RAND_MAX - (RAND_MAX % self.txsample.size)
        else:
            self._randlimit = RAND_MAX

    cdef void sample(self, TxPtrArray *txs, int num):
        cdef int newarraysize
        cdef int samplesize
        samplesize = self.txsample.size
        if not samplesize:
            return
        newarraysize = txs.size + num
        if newarraysize > txs.maxsize:
            txptrarray_resize(txs, newarraysize)
        for idx in range(num):
            ridx = randindex(samplesize, self._randlimit)
            txptrarray_append(txs, &self.txsample.txs[ridx])

    def __len__(self):
        return self.txsample.size

    def __dealloc__(self):
        txarray_deinit(self.txsample)


# ====================
# TxArray functions
# ====================
cdef TxArray txarray_init(int maxsize):
    cdef TxArray a
    a.size = 0
    a.maxsize = maxsize
    a.txs = <TxStruct *>malloc(maxsize*sizeof(TxStruct))
    return a


cdef void txarray_append(TxArray *a, TxStruct tx):
    if a.size == a.maxsize:
        txarray_resize(a, <int>((a.size+1)*OVERALLOCATE))
    a.txs[a.size] = tx
    a.size += 1


cdef void txarray_resize(TxArray *a, int newmaxsize):
    a.maxsize = newmaxsize
    if a.size > newmaxsize:
        a.size = newmaxsize
    a.txs = <TxStruct *>realloc(a.txs, newmaxsize*sizeof(TxStruct))


cdef void txarray_deinit(TxArray a):
    free(a.txs)


# ====================
# TxPtrArray functions
# ====================
cdef TxPtrArray txptrarray_init(int maxsize):
    cdef TxPtrArray a
    a.size = 0
    a.maxsize = maxsize
    a.txs = <TxStruct **>malloc(maxsize*sizeof(TxStruct *))
    return a


cdef void txptrarray_append(TxPtrArray *a, TxStruct *tx):
    if a.size == a.maxsize:
        txptrarray_resize(a, <int>((a.size+1)*OVERALLOCATE))
    a.txs[a.size] = tx
    a.size += 1


cdef void txptrarray_extend(TxPtrArray *a, TxPtrArray b):
    """Extend array a by the elements in array b."""
    cdef int newsize
    newsize = a.size + b.size
    if newsize >= a.maxsize:
        txptrarray_resize(a, <int>((newsize+1)*OVERALLOCATE))
    for idx in range(b.size):
        a.txs[a.size] = b.txs[idx]
        a.size += 1


cdef void txptrarray_resize(TxPtrArray *a, int newmaxsize):
    a.maxsize = newmaxsize
    if a.size > newmaxsize:
        a.size = newmaxsize
    a.txs = <TxStruct **>realloc(a.txs, newmaxsize*sizeof(TxStruct *))


cdef void txptrarray_copy(TxPtrArray source, TxPtrArray *dest):
    if dest.maxsize < source.size:
        txptrarray_resize(dest, source.size)
    dest.size = source.size
    for i in range(source.size):
        dest.txs[i] = source.txs[i]


cdef void txptrarray_deinit(TxPtrArray a):
    free(a.txs)


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


cdef poissonvariate(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution
    # #Generating_Poisson-distributed_random_variables
    cdef:
        float p
        int k
        float L

    if l > 30:
        return _normal_approx(l)
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return int(k - 1)


cdef _normal_approx(l):
    '''Normal approximation of the Poisson distribution.'''
    return int(normalvariate(l, l**0.5))
