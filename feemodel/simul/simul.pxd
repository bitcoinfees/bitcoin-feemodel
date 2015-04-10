from feemodel.simul.txsources cimport TxArray, TxPtrArray


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
        dict depmap
        list init_txids

    cdef _process_block(self, simblock)
