from __future__ import division

from feemodel.simul.txsources cimport TxStruct, TxPtrArray
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)

from collections import defaultdict
from bisect import insort

from feemodel.simul.stats import Capacity
from feemodel.simul.txsources import SimTx

rate_ratio_thresh = 0.9


cdef class Simul:

    cdef:
        readonly object pools, txsource, cap, stablefeerate, tx_emitter
        public float simtime
        public SimMempool mempool

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
        self.tx_emitter = self.txsource.get_emit_fn(feeratethresh=self.stablefeerate)
        self.simtime = 0.
        for simblock, blockinterval in self.pools.get_blockgen():
            self.simtime += blockinterval
            # Add new txs from the tx source to the queue
            self.tx_emitter(self.mempool.txqueue, blockinterval)
            self.mempool._process_block(simblock)
            simblock.sfr = max(simblock.sfr, self.stablefeerate)
            yield simblock


cdef class SimMempool:

    cdef:
        TxStruct *init_array
        TxPriorityQueue txqueue, txqueue_bak
        dict depmap
        list init_txids

    def __cinit__(self, init_entries):
        self.init_array = <TxStruct *>malloc(len(init_entries)*sizeof(TxStruct))

    def __init__(self, init_entries):
        init_txids = init_entries.keys()
        # Assert that there are no duplicate txids.
        assert len(set(init_txids)) == len(init_txids)
        # Keep a reference to the txid string objects,
        # for the sake of the char pointers in *init_array
        self.init_txids = init_txids
        self.txqueue = TxPriorityQueue()

        _depmap = defaultdict(list)
        for idx, entryitem in enumerate(init_entries.items()):
            txid, entry = entryitem
            self.init_array[idx].feerate = entry.feerate
            self.init_array[idx].size = entry.size
            self.init_array[idx].txid = txid
            if not entry.depends:
                self.txqueue.append(&self.init_array[idx])
            else:
                orphantx = OrphanTx(entry.depends)
                orphantx.set_tx(&self.init_array[idx])
                for dep in entry.depends:
                    # Assert that there are no hanging dependencies
                    assert dep in init_txids
                    _depmap[dep].append(orphantx)
        self.depmap = dict(_depmap)
        # For resetting the mempool to initial state.
        self.txqueue_bak = TxPriorityQueue()
        self.txqueue.txs_copy(self.txqueue_bak)

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

        for idx, simtx in enumerate(self.txqueue.get_simtxs()):
            entries['_'+str(idx)] = SimEntry(
                simtx.feerate, simtx.size, [])

        return entries

    def reset(self):
        cdef OrphanTx orphantx
        self.txqueue_bak.txs_copy(self.txqueue)
        for dependants in self.depmap.values():
            for orphantx in dependants:
                orphantx.depends.reset()

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

        self.txqueue.heapify()
        rejected_entries = TxPtrArray(maxsize=self.txqueue.size)
        blocktxs = TxPtrArray(maxsize=self.txqueue.size)

        while self.txqueue.size > 1:
            newtx = self.txqueue.heappop()
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
                            self.txqueue.heappush(orphantx.tx)
                else:
                    rejected_entries.append(newtx)
                    blocksize_ltd += 1
            else:
                rejected_entries.append(newtx)
                break
        self.txqueue.extend(rejected_entries.txs, rejected_entries.size)

        simblock.sfr = sfr + 1 if blocksize_ltd else minfeerate
        simblock.is_sizeltd = bool(blocksize_ltd)
        simblock.size = blocksize
        simblock.txs = blocktxs

    def __dealloc__(self):
        free(self.init_array)


class SimEntry(SimTx):

    def __init__(self, feerate, size, depends=None):
        super(SimEntry, self).__init__(feerate, size)
        if depends is None:
            depends = []
        self.depends = depends

    def __repr__(self):
        return "SimEntry({}, depends:{})".format(
            super(SimEntry, self).__repr__(), self.depends)


cdef class SimDepends:
    # TODO: get rid of this class; integrate with OrphanTx

    cdef set _depends, _depends_bak

    def __init__(self, depends):
        self._depends = set(depends) if depends else set()
        self._depends_bak = set(self._depends)

    cdef void remove(self, dependency):
        self._depends.remove(dependency)

    cdef void reset(self):
        self._depends = set(self._depends_bak)

    def __repr__(self):
        return "SimDepends({})".format(self._depends)

    def __iter__(self):
        return iter(self._depends)

    def __nonzero__(self):
        return bool(self._depends)


cdef class OrphanTx:
    '''Analogue of COrphan in miner.cpp.'''

    cdef:
        TxStruct *tx
        public SimDepends depends

    def __init__(self, list depends):
        assert depends
        self.depends = SimDepends(depends)

    cdef set_tx(self, TxStruct *tx):
        self.tx = tx


cdef class TxPriorityQueue(TxPtrArray):
    '''1-based indexing, max-heap.

    Index 0 is always NULL. So the actual size of the heap is
    self.size - 1.
    '''

    def __init__(self, int maxsize=0):
        self.append(NULL)

    cdef void _siftdown(self, int idx):
        cdef:
            int left, right, largerchild
            TxStruct *tmp

        while True:
            left = 2*idx
            if left < self.size:
                right = left + 1
                if right < self.size and self.txs[right].feerate > self.txs[left].feerate:
                    largerchild = right
                else:
                    largerchild = left
                if self.txs[largerchild].feerate > self.txs[idx].feerate:
                    tmp = self.txs[idx]
                    self.txs[idx] = self.txs[largerchild]
                    self.txs[largerchild] = tmp
                    idx = largerchild
                    continue
                break
            break

    cdef void heapify(self):
        startidx = self.size // 2
        for idx in range(startidx, 0, -1):
            self._siftdown(idx)

    cdef TxStruct* heappop(self):
        '''Extract the max.'''
        cdef TxStruct* besttx
        if self.size > 1:
            besttx = self.txs[1]
            self.txs[1] = self.pop()
            self._siftdown(1)
            return besttx
        return NULL

    cdef void heappush(self, TxStruct *tx):
        '''Push TxStruct * onto heap.'''
        cdef int idx, parent
        self.append(tx)

        idx = self.size - 1
        while idx > 1:
            parent = idx // 2
            if self.txs[idx].feerate > self.txs[parent].feerate:
                tmp = self.txs[idx]
                self.txs[idx] = self.txs[parent]
                self.txs[parent] = tmp
                idx = parent
            else:
                break
