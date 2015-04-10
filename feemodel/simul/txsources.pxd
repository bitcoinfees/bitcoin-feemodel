cdef struct TxStruct:
    unsigned long long feerate
    unsigned int size
    char *txid


cdef struct TxArray:
    TxStruct *txs
    int size
    int maxsize


cdef struct TxPtrArray:
    TxStruct **txs
    int size
    int maxsize


cdef class TxSampleArray:

    cdef:
        TxArray txsample
        int _randlimit

    cdef void sample(self, TxPtrArray *txs, int l)


# ====================
# TxArray functions
# ====================
cdef TxArray txarray_init(int maxsize)
cdef void txarray_append(TxArray *a, TxStruct tx)
cdef void txarray_resize(TxArray *a, int newmaxsize)
cdef void txarray_deinit(TxArray a)

# ====================
# TxPtrArray functions
# ====================
cdef TxPtrArray txptrarray_init(int maxsize)
cdef void txptrarray_append(TxPtrArray *a, TxStruct *tx)
cdef void txptrarray_extend(TxPtrArray *a, TxPtrArray b)
cdef void txptrarray_resize(TxPtrArray *a, int newmaxsize)
cdef void txptrarray_copy(TxPtrArray source, TxPtrArray *dest)
cdef void txptrarray_deinit(TxPtrArray a)
