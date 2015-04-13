from __future__ import division

from feemodel.simul.txsources cimport *
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)

from collections import defaultdict
from bisect import insort

from feemodel.simul.stats import Capacity
from feemodel.simul.txsources import SimTx

cap_ratio_thresh = 0.9


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
        # TODO: check edge conditions for feerates
        self.cap = Capacity(pools, txsource)
        # TODO: use all the tx points to calc stablefeerate
        self.stablefeerate = self.cap.calc_stablefeerate(cap_ratio_thresh)
        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")
        self.mempool = None
        self.simtime = 0.

    def run(self, init_entries=None):
        if init_entries is None:
            init_entries = {}
        self.mempool = SimMempool(init_entries)
        self.tx_emitter = self.txsource.get_emitter(self.mempool, feeratethresh=self.stablefeerate)
        self.simtime = 0.
        for simblock, blockinterval in self.pools.get_blockgen():
            self.simtime += blockinterval
            # Add new txs from the tx source to the queue
            self.tx_emitter(blockinterval)
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
            tx.feerate = entry.feerate
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

    cdef void _process_block(self, simblock):
        DEF MAXFEE = 2100000000000000
        cdef:
            int newblocksize, maxblocksize, blocksize, blocksize_ltd
            long long minfeerate, sfr
            TxStruct *newtx
            OrphanTx orphantx

        minfeerate = min(simblock.pool.minfeerate, MAXFEE)
        maxblocksize = simblock.pool.maxblocksize
        sfr = MAXFEE
        blocksize = 0
        blocksize_ltd = 0
        blocktxs = BlockTxs(self.txqueue.size)

        txqueue_heapify(self.txqueue)
        self.rejected_entries.size = 0

        while self.txqueue.size > 1:
            newtx = txqueue_heappop(&self.txqueue)
            if newtx.feerate >= minfeerate:
                newblocksize = newtx.size + blocksize
                if newblocksize <= maxblocksize:
                    if blocksize_ltd > 0:
                        blocksize_ltd -= 1
                    else:
                        # FIXME: SFR setting must be changed to match
                        #        stranding.py's definition
                        if newtx.feerate < sfr:
                            sfr = newtx.feerate

                    blocktxs.append(newtx)
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
        simblock.txs = blocktxs

    cdef void _process_deps(self, TxStruct *newtx):
        """Process dependants of tx newly added to a block.

        For each newtx added to a block, we remove newtx from the depends
        list of each of newtx's dependants. If the dependant has no more
        dependencies, then push it onto the tx queue.
        """
        cdef:
            int depidx, txindex
            OrphanTxPtrArray otxptrarray
        depidx = newtx - self.init_array.txs
        if depidx >= 0 and depidx < self.init_array.size:
            # Then newtx points to a transaction within self.init_array,
            # so it might have dependants.
            otxptrarray = self.orphanmap[depidx]
            for i in range(otxptrarray.size):
                txindex = orphantx_removedep(otxptrarray.otxptrs[i], depidx)
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


cdef class BlockTxs:
    """Python wrapper for TxPtrArray."""

    cdef:
        TxPtrArray txs

    def __cinit__(self, int maxsize):
        self.txs = txptrarray_init(maxsize)

    cdef void append(self, TxStruct *tx):
        txptrarray_append(&self.txs, tx)

    def get_simtxs(self):
        return [SimTx(self.txs.txs[idx].feerate, self.txs.txs[idx].size)
                for idx in range(self.txs.size)]

    def __len__(self):
        return self.txs.size

    def __dealloc__(self):
        txptrarray_deinit(self.txs)


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
