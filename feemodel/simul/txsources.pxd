cdef struct TxStruct:
    unsigned long long feerate
    unsigned int size
    # Take care to maintain elsewhere a reference to the Python string.
    char *txid


cdef class TxSampleArray:

    cdef:
        TxStruct* txsample
        int size
        int _randlimit

    cdef void sample(self, TxPtrArray txs, int l)


cdef class TxPtrArray:

    cdef:
        TxStruct **txs
        int size
        int totalsize

    cdef void append(self, TxStruct *tx)
    cdef void extend(self, TxStruct **txs, int size)
    cdef TxStruct* pop(self)
    cdef void clear(self)
    cdef void _resize(self, int newtotalsize)
