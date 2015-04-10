from __future__ import division

from feemodel.simul.txsources cimport *
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)

from collections import defaultdict
from bisect import insort

from feemodel.simul.stats import Capacity
from feemodel.simul.txsources import SimTx

rate_ratio_thresh = 0.9


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
        self.stablefeerate = self.cap.calc_stablefeerate(rate_ratio_thresh)
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
        self.txqueue = txqueue_init(len(init_entries))
        self.txqueue_bak = txqueue_init(len(init_entries))
        self.rejected_entries = txptrarray_init(len(init_entries))

    def __init__(self, init_entries):
        cdef:
            TxStruct tx
            TxStruct *txptr
        init_txids = init_entries.keys()
        # Assert that there are no duplicate txids.
        assert len(set(init_txids)) == len(init_txids)
        # Keep a reference to the txid string objects,
        # for the sake of the char pointers in *init_array
        self.init_txids = init_txids

        _depmap = defaultdict(list)
        for idx, (txid, entry) in enumerate(init_entries.items()):
            tx.feerate = entry.feerate
            tx.size = entry.size
            tx.txid = txid
            txarray_append(&self.init_array, tx)
            txptr = &self.init_array.txs[idx]
            if not entry.depends:
                txptrarray_append(&self.txqueue, txptr)
            else:
                orphantx = OrphanTx(entry.depends)
                orphantx.tx = txptr
                for dep in entry.depends:
                    # Assert that there are no hanging dependencies
                    assert dep in init_txids
                    _depmap[dep].append(orphantx)
        self.depmap = dict(_depmap)
        # For resetting the mempool to initial state.
        txptrarray_copy(self.txqueue, &self.txqueue_bak)

    def get_entries(self):
        cdef OrphanTx orphantx
        cdef TxStruct *tx
        entries = {}
        orphans = set()
        for orphan_list in self.depmap.values():
            for orphantx in orphan_list:
                if orphantx.depends:
                    orphans.add(orphantx)

        for orphantx in orphans:
            tx = orphantx.tx
            entries[tx.txid] = SimEntry(
                tx.feerate, tx.size, list(orphantx.depends))

        # FIXME: Some non-depends txs have txids!
        for idx in range(1, self.txqueue.size):
            tx = self.txqueue.txs[idx]
            entries['_'+str(idx)] = SimEntry(tx.feerate, tx.size)

        return entries

    def reset(self):
        cdef OrphanTx orphantx
        txptrarray_copy(self.txqueue_bak, &self.txqueue)
        for dependants in self.depmap.values():
            for orphantx in dependants:
                orphantx.reset_deps()

    cdef _process_block(self, simblock):
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

        txqueue_heapify(self.txqueue)
        blocktxs = BlockTxs(self.txqueue.size)
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

                    if newtx.txid is NULL:
                        continue
                    dependants = self.depmap.get(newtx.txid)
                    if dependants is None:
                        continue
                    # TODO: don't use a list for dependants but a cdef class
                    for orphantx in dependants:
                        orphantx.depends.remove(newtx.txid)
                        if not orphantx.depends:
                            txqueue_heappush(&self.txqueue, orphantx.tx)
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

    def __dealloc__(self):
        txarray_deinit(self.init_array)
        txptrarray_deinit(self.txqueue)
        txptrarray_deinit(self.txqueue_bak)
        txptrarray_deinit(self.rejected_entries)


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


# #cdef struct OrphanTx:
# #    TxStruct *tx
# #    int *depends
# #    int *removed
# #
# #
# #cdef orphantx_init(TxStruct *tx, list depends):
# #    cdef:
# #        OrphanTx orphan
# #        int n
# #    orphan.tx = tx
# #    n = len(depends)
# #    orphan.depends = <int *>malloc(n*sizeof(int))
# #    orphan.removed = <int *>malloc(n*sizeof(int))
# #    return orphan
# #
# #
# #cdef orphantx_deinit(OrphanTx orphan):
# #    free(orphan.depends)
# #    free(orphan.removed)
# #
# #
# #cdef orphantx_removedep(OrphanTx orphan, int dep):
# #    pass


cdef class OrphanTx:
    '''Analogue of COrphan in miner.cpp.'''

    cdef:
        TxStruct *tx
        set depends, _depends_bak

    def __init__(self, list depends):
        assert depends
        self.depends = set(depends)
        self._depends_bak = set(depends)

    cdef void reset_deps(self):
        self.depends = set(self._depends_bak)


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
