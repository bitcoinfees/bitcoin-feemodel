from __future__ import division

from libc.stdlib cimport rand, srand, RAND_MAX
from libc.time cimport time
from libc.limits cimport ULONG_MAX
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)
from feemodel.simul.simul cimport SimMempool

from math import exp
from random import random, normalvariate, getrandbits
from bisect import bisect_left
from itertools import groupby
from operator import attrgetter

from tabulate import tabulate

from feemodel.util import DataSample, cumsum_gen, StepFunction

DEF OVERALLOCATE = 2  # This better be > 1.

cdef unsigned long MAX_FEERATE = ULONG_MAX - 1


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

    def check(self):
        if self.txrate and not self.txsample:
            raise ValueError("Non-zero txrate with empty txsample.")

    def get_emitter(self, SimMempool mempool not None, feeratethresh=0):
        cdef int i
        self.check()
        txsample_filtered = filter(lambda tx: tx.feerate >= feeratethresh,
                                   self.txsample)
        txsample_array = TxSampleArray(txsample_filtered)
        if self.txrate:
            filtered_txrate = (len(txsample_filtered) / len(self.txsample) *
                               self.txrate)
        else:
            filtered_txrate = 0

        # Sanity check on possible undefined behavior of pointer comparisons
        # (made use of in SimMempool._process_deps)
        for i in range(txsample_array.txsample.size):
            assert not (mempool.init_array.txs <=
                        txsample_array.txsample.txs + i <
                        mempool.init_array.txs + mempool.init_array.size)

        srand(getrandbits(8*sizeof(unsigned int)))

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

    def get_byteratefn(self):
        # FIXME: doesn't work with samplesize = 1
        self.check()
        n = len(self.txsample)

        def byterate_groupsum(grouptuple):
            """Sum all tx sizes in a feerate group.

            A feerate group is all the txs which have the same feerate.
            """
            return sum([tx.size for tx in grouptuple[1]])*self.txrate/n

        txsample = sorted(self.txsample, key=attrgetter("feerate"), reverse=True)
        feerates = sorted(set(map(attrgetter("feerate"), txsample)))
        byterates = list(cumsum_gen(
            groupby(txsample, attrgetter("feerate")), mapfn=byterate_groupsum))
        byterates.reverse()

        if not feerates:
            feerates = [0]
            byterates = [0.]
        elif feerates[0] != 0:
            feerates.insert(0, 0)
            byterates.insert(0, byterates[0])

        return StepFunction(feerates, byterates)

    def calc_mean_byterate(self):
        '''Calculate the mean byterate.

        Returns the mean byterate with its standard error, computed using a
        normal approximation.
        '''
        self.check()
        if not self.txsample:
            return 0, 0

        d = DataSample([tx.size*self.txrate for tx in self.txsample])
        d.calc_stats()
        return d.mean, d.std / len(self.txsample)**0.5

    def __str__(self):
        if not self:
            return "No txsample."
        byteratefn = self.get_byteratefn()
        headers = ['Feerate', 'Cumul. Byterate (bytes/s)']
        table = list(byteratefn.approx())
        mean_byterate, std = self.calc_mean_byterate()
        s1 = tabulate(table, headers=headers)
        s2 = "\nMean byterate (std): {} ({})".format(mean_byterate, std)
        return s1 + s2

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
            tx.feerate = min(simtx.feerate, MAX_FEERATE)
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
