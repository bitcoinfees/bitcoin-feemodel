from feemodel.measurement import TxRates
from feemodel.pools import PoolEstimator
from feemodel.util import proxy
from feemodel.queue import QEstimator
from random import expovariate

blockRate = 1.0/600 # Once every 10 minutes

class Simul(object):
    def __init__(self):
        self.mempool = {}
        try:
            self.pe = PoolEstimator.loadObject()
        except IOError:
            logWrite("Unable to load poolEstimator")
            self.pe = PoolEstimator()
        try:
            self.tr = TxRates.loadObject()
        except IOError:
            logWrite("Unable to load txRates.")
            self.tr = TxRates()

        self.feeClassValues = None

    def steadyState(self, rateInterval, currHeight=None):
        if not currHeight:
            currHeight = proxy.getblockcount()
        txRate = self.tr.calcRates((currHeight-rateInterval+1, currHeight+1))
        q = QEstimator(self.getFeeClassValues(100000, 1000, 5000))
        minFeeClass = min(q.feeClassValues)
        tidxOffset = 0
        self.mempool = {}
        # txRate = 2

        for i in range(1000):
            t = expovariate(blockRate)
            txSample = self.tr.generateTxSample(t*txRate)
            self.mempool.update({str(tidx + tidxOffset): tx for tidx, tx in enumerate(txSample)
                if tx['feeRate'] >= minFeeClass})
            tidxOffset += len(txSample)
            name, pool = self.pe.selectRandomPool()
            sfr = self.processBlock(pool)
            q.nextBlock(i, t, sfr)
            if len(self.mempool) > 100000:
                print("Too many transactions!")
                break

        return q.getStats()

    def processBlock(self, pool):
        maxBlockSize = pool.maxBlockSize
        minFeeRate = pool.minFeeRate 
        
        blockSize = 0
        strandingFeeRate = float("inf")
        blockSizeLimited = False

        txDeps = {}
        txNoDeps = []
        for txid,entry in self.mempool.iteritems():
            if not entry['depends']:
                txNoDeps.append((txid, entry['size'], entry['feeRate']))
            else:
                txDeps.update({txid: entry})

        txNoDeps.sort(key=lambda x: x[2])

        while txNoDeps:
            newTx = txNoDeps.pop()
            if newTx[2] >= minFeeRate:
                if newTx[1] + blockSize <= maxBlockSize:
                    blockSizeLimited = False
                    blockSize += newTx[1]
                    strandingFeeRate = newTx[2]
                    depAdded = False
                    for txid, entry in txDeps.iteritems():
                        try:
                            entry['depends'].remove(newTx[0])
                        except ValueError:
                            pass
                        else:
                            if not entry['depends']:
                                del txDeps[txid]
                                txNoDeps.append((txid, entry['size'], entry['feeRate']))
                                depAdded = True
                    if depAdded:
                        txNoDeps.sort(key=lambda x: x[2])
                    del self.mempool[newTx[0]]
                else:
                    blockSizeLimited = True
            else:
                break

        return strandingFeeRate if blockSizeLimited else minFeeRate

    def getFeeClassValues(self, maxMFR, minSpacing, maxSpacing):
        poolMFR = self.pe.getPoolMFR()
        poolMFR = [f for f in poolMFR if f != float("inf")]
        feeRates = range(min(poolMFR), maxMFR, maxSpacing)
        feeRates.extend(poolMFR)
        feeRates.sort(reverse=True)
        
        prevFeeRate = feeRates[0]
        feeClassValues = [prevFeeRate]
        for feeRate in feeRates[1:]:
            if prevFeeRate - feeRate >= minSpacing:
                feeClassValues.append(feeRate)
                prevFeeRate = feeRate

        return feeClassValues






