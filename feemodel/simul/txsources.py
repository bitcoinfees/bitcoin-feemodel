from bisect import bisect


class SimTx(object):
    def __init__(self, txid, size, feerate):
        self.txid = txid
        self.size = size
        self.feerate = feerate

    def __cmp__(self, other):
        return cmp(self.feerate, other.feerate)

    def __repr__(self):
        return "SimTx{txid: %s, size: %d, feerate: %d}" % (
            self.txid, self.size, self.feerate)


class SimpleTxSource(object):
    def __init__(self, txsize, txfeerate, txrate):
        self.txsize = txsize
        self.txfeerate = txfeerate
        self.txrate = txrate

    def generate_txs(self, time_interval, cutoff=0):
        if self.txfeerate >= cutoff:
            return [SimTx('', self.txsize, self.txfeerate)
                    for i in range(int(self.txrate*time_interval))]
        else:
            return []

    def get_byterates(self, feepoints):
        byterates = [0.]*len(feepoints)
        fee_idx = bisect(feepoints, self.txfeerate)
        if fee_idx > 0:
            byterates[fee_idx-1] = self.txrate*self.txsize
        return byterates

    def get_feepoints(self):
        return [self.txfeerate]
