from __future__ import division

from feemodel.simul.txsources cimport TxStruct, TxPtrArray
from cpython.mem cimport (PyMem_Malloc as malloc,
                          PyMem_Realloc as realloc,
                          PyMem_Free as free)

from collections import defaultdict
from copy import copy
from bisect import insort

from feemodel.simul.stats import Capacity
from feemodel.simul.txsources import SimTx

rate_ratio_thresh = 0.9


cdef class Simul:

    cdef readonly object pools, tx_source, cap, stablefeerate, txgen
    cdef public SimMempool mempool

    def __init__(self, pools, tx_source):
        self.pools = pools
        self.tx_source = tx_source
        # TODO: check edge conditions for feerates
        self.cap = Capacity(pools, tx_source)
        self.stablefeerate = self.cap.calc_stablefeerate(rate_ratio_thresh)
        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")
        self.mempool = None

    def run(self, init_entries=None):
        if init_entries is None:
            init_entries = []
        self.mempool = SimMempool(init_entries)
        self.txgen = self.tx_source.get_c_txgen(feeratethresh=self.stablefeerate)
        for simblock in self.pools.blockgen():
            # Add new txs to the txqueue
            self.txgen(self.mempool.txqueue, simblock.interval)
            self.mempool._process_block(simblock)
            simblock.sfr = max(simblock.sfr, self.stablefeerate)
            yield simblock


cdef class SimMempool:

    cdef:
        TxStruct *init_array
        TxPriorityQueue txqueue, txqueue_bak
        dict depmap
        object init_entries

    def __cinit__(self, init_entries):
        self.init_array = <TxStruct *>malloc(len(init_entries)*sizeof(TxStruct))

    def __init__(self, init_entries):
        self.init_entries = init_entries  # Preserve the txid string references
        self.txqueue = TxPriorityQueue()

        _depmap = defaultdict(list)

        txids = [entry.txid for entry in init_entries]
        # Assert that there are no duplicate txids.
        assert len(set(txids)) == len(txids)
        for idx, entry in enumerate(init_entries):
            self.init_array[idx].feerate = entry.tx.feerate
            self.init_array[idx].size = entry.tx.size
            self.init_array[idx].txid = entry.txid
            if not entry.depends:
                self.txqueue.append(&self.init_array[idx])
            else:
                orphantx = OrphanTx(entry.depends)
                orphantx.set_tx(&self.init_array[idx])
                for dep in entry.depends:
                    # Assert that there are no hanging dependencies
                    assert dep in txids
                    _depmap[dep].append(orphantx)
        self.depmap = dict(_depmap)
        # For resetting the mempool to initial state.
        self.txqueue_bak = copy(self.txqueue)

    @property
    def entries(self):
        cdef OrphanTx orphantx
        cdef TxStruct *tx
        entries = {}
        orphans = set()
        for orphan_list in self.depmap.values():
            for orphantx in orphan_list:
                if orphantx.depends:
                    orphans.add(orphantx)

        for orphantx in orphans:
            pass
            # tx = orphantx.tx
            # entries[tx.txid] = SimEntry()

        # entries = []
        # entries.extend(
        #     [SimEntry(tx[2], SimTx(tx[0], tx[1])) for tx in self._nodeps])
        # entries.extend(
        #     [SimEntry(txid, SimTx(entry[0][0], entry[0][1]), entry[1])
        #      for txid, entry in self._havedeps.items()])
        # return entries

    def reset(self):
        cdef OrphanTx orphantx
        self.txqueue = copy(self.txqueue_bak)
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

        pool = simblock.poolinfo[1]
        minfeerate = min(pool.minfeerate, MAXFEE)
        maxblocksize = pool.maxblocksize
        sfr = MAXFEE
        blocksize = 0
        blocksize_ltd = 0

        self.txqueue.heapify()
        rejected_entries = TxPtrArray(init_size=len(self.txqueue))
        blocktxs = TxPtrArray(init_size=len(self.txqueue))
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


# #class SimEntry(SimTx):
# #
# #    def __init__(self, feerate, size, depends):
# #        self.txid = txid
# #        self.tx = simtx
# #        if isinstance(depends, SimDepends):
# #            self.depends = depends
# #        else:
# #            self.depends = SimDepends(depends)
# #
# #    @classmethod
# #    def from_mementry(cls, txid, entry):
# #        return cls(txid, SimTx(entry.feerate, entry.size),
# #                   depends=entry.depends)
# #
# #    def __repr__(self):
# #        return "SimEntry({}, {}, {})".format(
# #            self.txid, repr(self.tx), repr(self.depends))


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


cdef class SimDepends:

    cdef set _depends, _depends_bak

    def __init__(self, depends):
        self._depends = set(depends) if depends else set()
        self._depends_bak = set(self._depends)

    cdef void remove(self, dependency):
        self._depends.remove(dependency)

    cdef void reset(self):
        self._depends = set(self._depends_bak)

    def repr(self):
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

    def __init__(self, SimDepends depends):
        self.depends = depends

    cdef set_tx(self, TxStruct *tx):
        self.tx = tx


cdef class TxPriorityQueue(TxPtrArray):
    '''1-based indexing, max-heap.'''

    def __init__(self, int init_size=0):
        self.append(NULL)

    cdef void _siftdown(self, int idx):
        cdef:
            int left, right, largerchild
            TxStruct *tmp

        while idx < self.size:
            left = 2*idx
            right = left + 1

            if left < self.size:
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
            if self.size > 2:
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

    def __copy__(self):
        newqueue = TxPriorityQueue()
        newqueue._resize(self.totalsize)
        newqueue.extend(&self.txs[1], self.size-1)
        return newqueue
