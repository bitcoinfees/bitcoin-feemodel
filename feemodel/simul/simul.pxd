from feemodel.simul.txsources cimport *


cdef struct OrphanTx:
    int txindex
    int *depends
    int *removed
    int numdeps
    int maxdeps


cdef struct OrphanTxPtrArray:
    # Fixed size array of pointers to orphan txs.
    OrphanTx **otxptrs
    int size


cdef struct OrphanTxArray:
    # Fixed size array of orphan txs.
    OrphanTx *otxs
    int size


cdef class Simul:

    cdef:
        readonly object cap, pools, txsource
        readonly int stablefeerate
        public float simtime
        public SimMempool mempool
        object tx_emitter


cdef class SimMempool:

    cdef:
        TxArray init_array
        TxPtrArray txqueue, txqueue_bak, rejected_entries
        OrphanTxPtrArray *orphanmap
        OrphanTxArray orphans
        list txidlist

    cdef void _process_block(self, SimBlock simblock)
    cdef void _process_deps(self, TxStruct *newtx)
    cdef void _reset_orphan_deps(self)


cdef class SimBlock:

    cdef:
        public object poolname, pool, size, sfr, is_sizeltd, _txs
        TxPtrArray _txptrs
