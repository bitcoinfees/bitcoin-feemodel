from __future__ import division

from libc.limits cimport ULONG_MAX
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)
from feemodel.simul.txsources cimport *

from feemodel.simul.stats import Capacity
from feemodel.simul.txsources import SimTx

UTILIZATION_THRESH = 0.9
cdef unsigned long MAX_FEERATE = ULONG_MAX - 1
cdef int MAX_QUEUESIZE = 1000000  # Max num of txs in mempool heap


class SimEntry(SimTx):

    def __init__(self, feerate, size, depends=None):
        super(SimEntry, self).__init__(feerate, size)
        if depends is None:
            depends = []
        self.depends = depends

    def __repr__(self):
        return "SimEntry({}, depends:{})".format(
            super(SimEntry, self).__repr__(), self.depends)


cdef class Simul:

    def __init__(self, pools, txsource):
        self.pools = pools
        self.txsource = txsource
        self.cap = Capacity(pools, txsource)
        self.stablefeerate = self.cap.calc_stablefeerate(UTILIZATION_THRESH)
        self.mempool = None
        self.tx_emitter = None
        self.simtime = 0.
        # Non-zero capacity is guaranteed by SimPools.check

    def run(self, init_entries=None):
        if init_entries is None:
            init_entries = {}
        self.mempool = SimMempool(init_entries)
        self.tx_emitter = self.txsource.get_emitter(self.mempool, feeratethresh=self.stablefeerate)
        self.simtime = 0.
        for simblock, blockinterval in self.pools.blockgen():
            self.simtime += blockinterval
            # Add new txs from the tx source to the queue
            self.tx_emitter(blockinterval)
            # This is a fail-safe in the event of instability.
            # This should not normally happen, because of stablefeerate calcs.
            if self.mempool.txqueue.size > MAX_QUEUESIZE:
                raise ValueError("Max queuesize reached.")
            self.mempool._process_block(simblock)
            simblock.sfr = max(simblock.sfr, self.stablefeerate)
            yield simblock


cdef class SimMempool:

    def __cinit__(self, init_entries):
        self.init_array = txarray_init(len(init_entries))
        self.txqueue = txqueue_init(len(init_entries)+1)
        self.txqueue_bak = txqueue_init(len(init_entries)+1)
        self.rejected_entries = txptrarray_init(len(init_entries))

        self.orphans.otxs = NULL
        self.orphanmap = <OrphanTxPtrArray *>malloc(len(init_entries)*sizeof(OrphanTxPtrArray))
        for i in range(len(init_entries)):
            self.orphanmap[i].otxptrs = NULL

    def __init__(self, init_entries):
        cdef:
            TxStruct tx
            OrphanTxPtrArray otxptrarray

        txidmap = {}
        self.txidlist = [None]*len(init_entries)
        py_orphans = []
        for idx, (txid, entry) in enumerate(init_entries.items()):
            txidmap[txid] = idx
            self.txidlist[idx] = txid
            tx.feerate = min(entry.feerate, MAX_FEERATE)
            tx.size = entry.size
            txarray_append(&self.init_array, tx)
            if not entry.depends:
                txptrarray_append(&self.txqueue, &self.init_array.txs[idx])
            else:
                py_orphans.append((idx, entry.depends))

        for idx, depends in py_orphans:
            if any([dep not in txidmap for dep in depends]):
                raise ValueError("There are hanging dependencies.")

        self.orphans = otxarray_init(len(py_orphans))
        py_orphanmap = [[] for i in range(len(init_entries))]
        for oidx, (idx, depends) in enumerate(py_orphans):
            depends = map(lambda txid: txidmap[txid], depends)
            self.orphans.otxs[oidx] = orphantx_init(idx, depends)
            for depidx in depends:
                py_orphanmap[depidx].append(oidx)

        for idx, dependants in enumerate(py_orphanmap):
            otxptrarray = otxptrarray_init(len(dependants))
            for optridx, oidx in enumerate(dependants):
                otxptrarray.otxptrs[optridx] = &self.orphans.otxs[oidx]
            self.orphanmap[idx] = otxptrarray

        # For resetting the mempool to initial state.
        txptrarray_copy(self.txqueue, &self.txqueue_bak)

    def get_entries(self):
        cdef:
            TxStruct *txptr
            TxStruct tx
            OrphanTx orphan
        entries = {}
        for idx in range(1, self.txqueue.size):
            txptr = self.txqueue.txs[idx]
            init_idx = txptr - self.init_array.txs
            if init_idx >= 0 and init_idx < self.init_array.size:
                txid = self.txidlist[init_idx]
            else:
                txid = '_' + str(idx)
            entries[txid] = SimEntry(txptr.feerate, txptr.size)

        for idx in range(self.orphans.size):
            orphan = self.orphans.otxs[idx]
            if orphan.numdeps == 0:
                continue
            tx = self.init_array.txs[orphan.txindex]
            txid = self.txidlist[orphan.txindex]
            depends = [
                self.txidlist[orphan.depends[i]] for i in range(orphan.maxdeps)
                if not orphan.removed[i]
            ]
            entries[txid] = SimEntry(tx.feerate, tx.size, depends=depends)

        return entries

    def reset(self):
        txptrarray_copy(self.txqueue_bak, &self.txqueue)
        self._reset_orphan_deps()

    cdef void _process_block(self, SimBlock simblock):
        cdef:
            unsigned long newblocksize, maxblocksize, blocksize, blocksize_ltd
            unsigned long minfeerate, sfr
            TxStruct *newtx
            OrphanTx orphantx
            TxPtrArray blocktxs

        minfeerate = min(simblock.pool.minfeerate, MAX_FEERATE)
        maxblocksize = simblock.pool.maxblocksize
        sfr = MAX_FEERATE
        blocksize = 0
        blocksize_ltd = 0
        blocktxs = txptrarray_init(self.txqueue.size)

        txqueue_heapify(self.txqueue)
        self.rejected_entries.size = 0

        while self.txqueue.size > 1:
            newtx = txqueue_heappop(&self.txqueue)
            if newtx.feerate >= minfeerate:
                newblocksize = newtx.size + blocksize
                if newblocksize <= maxblocksize:
                    if blocksize_ltd > 0:
                        blocksize_ltd -= 1
                    elif newtx.feerate < sfr:
                        sfr = newtx.feerate

                    txptrarray_append(&blocktxs, newtx)
                    blocksize = newblocksize
                    self._process_deps(newtx)
                else:
                    txptrarray_append(&self.rejected_entries, newtx)
                    blocksize_ltd += 1
            else:
                txptrarray_append(&self.rejected_entries, newtx)
                break
        txptrarray_extend(&self.txqueue, self.rejected_entries)

        simblock.sfr = sfr + 1 if blocksize_ltd else minfeerate
        simblock.is_sizeltd = bool(blocksize_ltd)
        simblock.size = blocksize
        simblock._txptrs = blocktxs

    cdef void _process_deps(self, TxStruct *newtx):
        """Process dependants of tx newly added to a block.

        For each newtx added to a block, we remove newtx from the depends
        list of each of newtx's dependants. If the dependant has no more
        dependencies, then push it onto the tx queue.
        """
        cdef:
            int depidx, txindex
            OrphanTxPtrArray dependants

        if self.init_array.txs <= newtx < self.init_array.txs + self.init_array.size:
            # Then newtx points to a transaction within self.init_array,
            # so it might have dependants.
            # WARNING: we're not 100% sure that this expression always evaluates
            #          false if newtx is not part of the array.
            depidx = newtx - self.init_array.txs
            dependants = self.orphanmap[depidx]
            for i in range(dependants.size):
                txindex = orphantx_removedep(dependants.otxptrs[i], depidx)
                if txindex >= 0:
                    txqueue_heappush(&self.txqueue, &self.init_array.txs[txindex])

    cdef void _reset_orphan_deps(self):
        """Reset the depends list of orphans."""
        for i in range(self.orphans.size):
            orphantx_resetdeps(&self.orphans.otxs[i])

    def __dealloc__(self):
        txarray_deinit(self.init_array)
        txptrarray_deinit(self.txqueue)
        txptrarray_deinit(self.txqueue_bak)
        txptrarray_deinit(self.rejected_entries)

        otxarray_deinit(self.orphans)
        for i in range(self.init_array.size):
            otxptrarray_deinit(self.orphanmap[i])
        free(self.orphanmap)


cdef class SimBlock(object):

    def __cinit__(self, poolname, pool):
        self._txptrs.txs = NULL

    def __init__(self, poolname, pool):
        self.poolname = poolname
        self.pool = pool
        self.size = 0
        self.sfr = float("inf")
        self.is_sizeltd = None
        self._txs = None

    property txs:

        def __get__(self):
            '''Get the block transactions as a SimTx list.

            For efficiency, we keep the txs as a TxPtrArray (as assigned in
            SimMempool._process_block), and only instantiate the SimTxs
            the first time you access it.

            Take note that if the Simul instance that produced this SimBlock
            becomes unreferenced, the tx memory to which self._txptrs.txs
            points will become deallocated, and bad things will happen.

            TL;DR - if you want to access this property, make sure you keep a
            reference to the Simul instance, at least up until the first time
            you access this property.
            '''
            if self._txs is None:
                if self._txptrs.txs is NULL:
                    return []
                self._txs = [
                    SimTx(self._txptrs.txs[i].feerate,
                          self._txptrs.txs[i].size)
                    for i in range(self._txptrs.size)]
            return self._txs

    def __repr__(self):
        return "SimBlock(pool: {}, numtxs: {}, size: {}, sfr: {})".format(
            self.poolname, len(self.txs), self.size, self.sfr)

    def __dealloc__(self):
        txptrarray_deinit(self._txptrs)


# =============
# OrphanTxPtrArray
# =============
cdef OrphanTxPtrArray otxptrarray_init(int size):
    cdef OrphanTxPtrArray otxptrarray
    otxptrarray.size = size
    otxptrarray.otxptrs = <OrphanTx **>malloc(size*sizeof(OrphanTx *))
    return otxptrarray

cdef void otxptrarray_deinit(OrphanTxPtrArray otxptrarray):
    free(otxptrarray.otxptrs)

# =============
# OrphanTxArray
# =============
cdef OrphanTxArray otxarray_init(int size):
    cdef OrphanTxArray otxarray
    otxarray.size = size
    otxarray.otxs = <OrphanTx *>malloc(size*sizeof(OrphanTx))
    return otxarray

cdef void otxarray_deinit(OrphanTxArray otxarray):
    for i in range(otxarray.size):
        orphantx_deinit(otxarray.otxs[i])

# =============
# OrphanTx
# =============
cdef OrphanTx orphantx_init(int txindex, list depends):
    cdef:
        OrphanTx orphan
        int n
    orphan.txindex = txindex
    n = len(depends)
    orphan.depends = <int *>malloc(n*sizeof(int))
    orphan.removed = <int *>malloc(n*sizeof(int))
    orphan.numdeps = n
    orphan.maxdeps = n
    for i in range(n):
        orphan.depends[i] = depends[i]
        orphan.removed[i] = 0
    return orphan

cdef void orphantx_deinit(OrphanTx orphan):
    free(orphan.depends)
    free(orphan.removed)

cdef int orphantx_removedep(OrphanTx *orphan, int dep):
    """Remove a dependency.

    Returns txindex if there are no deps left, -1 otherwise.
    """
    for i in range(orphan.maxdeps):
        if orphan.depends[i] == dep:
            orphan.removed[i] = 1
            orphan.numdeps -= 1
            break
    if orphan.numdeps == 0:
        return orphan.txindex
    return -1

cdef void orphantx_resetdeps(OrphanTx *orphan):
    for i in range(orphan.maxdeps):
        orphan.removed[i] = 0
    orphan.numdeps = orphan.maxdeps


# =============
# Heap stuff
# =============
cdef TxPtrArray txqueue_init(int maxsize):
    cdef TxPtrArray txqueue
    txqueue = txptrarray_init(maxsize)
    txptrarray_append(&txqueue, NULL)
    return txqueue

cdef void txqueue_heappush(TxPtrArray *txqueue, TxStruct *tx):
    '''Push TxStruct * onto heap.'''
    cdef int idx, parent
    txptrarray_append(txqueue, tx)

    idx = txqueue.size - 1
    while idx > 1:
        parent = idx // 2
        if txqueue.txs[idx].feerate > txqueue.txs[parent].feerate:
            tmp = txqueue.txs[idx]
            txqueue.txs[idx] = txqueue.txs[parent]
            txqueue.txs[parent] = tmp
            idx = parent
        else:
            break

cdef TxStruct* txqueue_heappop(TxPtrArray *txqueue):
    """Extract the max."""
    cdef TxStruct *besttx
    if txqueue.size > 1:
        besttx = txqueue.txs[1]
        txqueue.txs[1] = txqueue.txs[txqueue.size-1]
        txqueue.size -= 1
        txqueue_siftdown(txqueue[0], 1)
        return besttx
    return NULL

cdef void txqueue_heapify(TxPtrArray txqueue):
    cdef int startidx
    startidx = txqueue.size // 2
    for idx in range(startidx, 0, -1):
        txqueue_siftdown(txqueue, idx)

cdef void txqueue_siftdown(TxPtrArray txqueue, int idx):
    cdef:
        int left, right, largerchild
        TxStruct *tmp

    while True:
        left = 2*idx
        if left < txqueue.size:
            right = left + 1
            if right < txqueue.size and txqueue.txs[right].feerate > txqueue.txs[left].feerate:
                largerchild = right
            else:
                largerchild = left
            if txqueue.txs[largerchild].feerate > txqueue.txs[idx].feerate:
                tmp = txqueue.txs[idx]
                txqueue.txs[idx] = txqueue.txs[largerchild]
                txqueue.txs[largerchild] = tmp
                idx = largerchild
                continue
            break
        break
