from feemodel.measurement import TxRates
from feemodel.pools import PoolEstimator
from feemodel.util import proxy
from feemodel.queue import QEstimator
from feemodel.config import config
from bitcoin.core import COIN
from random import expovariate
from copy import deepcopy

blockRate = config['simul']['blockRate'] # Once every 10 minutes
# blockRate = 1./60000
rateRatioThresh = 0.9
convergeThresh = 0.0001

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

    def initCalcs(self, rateInterval):
        self.feeClassValues = self.getFeeClassValues(100000, 1000, 5000)
        self.feeRates, self.processingRate, self.processingRateUpper = self.pe.getProcessingRate(blockRate)
        self.txByteRate, self.txRate = self.tr.getByteRate(rateInterval, self.feeRates)

        self.stableFeeRate = None
        for idx in range(len(self.feeRates)):
            if self.txByteRate[idx] / self.processingRate[idx] < rateRatioThresh:
                self.stableFeeRate = self.feeRates[idx]
                break
        if not self.stableFeeRate:
            raise ValueError("The queue is not stable - arrivals exceed processing for all feerates.")

    def conditional(self, rateInterval, mempool):
        self.initCalcs(rateInterval)
        for entry in mempool.itervalues():
            if not 'feeRate' in entry:
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']
        waitTimes = {feeRate: [] for feeRate in self.feeClassValues}
        totaltime = 0.

        for i in range(10000):
            stranded = self.feeClassValues[:]
            self.mempool = deepcopy(mempool)
            blockIdx = 0
            totaltime = 0.
            while stranded:
                t = self.addToMempool(blockIdx)
                sfr = self.processBlock()
                totaltime += t
                blockIdx += 1
                strandedDel = []
                for feeRate in stranded:
                    if feeRate >= sfr:
                        waitTimes[feeRate].append(totaltime)
                        strandedDel.append(feeRate)
                for feeRate in strandedDel:
                    stranded.remove(feeRate)

        return waitTimes

    def steadyState(self, rateInterval, mempool=None):
        self.initCalcs(rateInterval)
        if not mempool:
            self.mempool = {}
        else:
            self.mempool = mempool

        for entry in self.mempool.itervalues():
            if not 'feeRate' in entry:
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']

        q = QEstimator(self.feeClassValues)
        convergeCount = 0
        for i in range(1000):
            t = self.addToMempool(i)
            sfr = self.processBlock()
            d = q.nextBlock(i, t, sfr)
            # if d <= convergeThresh:
            #     convergeCount += 1
            # else:
            #     convergeCount = 0
            # if convergeCount >= 10:
            #     break
        print("Num iters: %d" % i)
        return q.getStats()

    def addToMempool(self, blockIdx):
        t = expovariate(blockRate)
        txSample = self.tr.generateTxSample(t*self.txRate)
        self.mempool.update({'%d_%d' % (blockIdx, tidx): tx for tidx, tx in enumerate(txSample)
            if tx['feeRate'] >= self.stableFeeRate})

        return t

    def processBlock(self):
        name, pool = self.pe.selectRandomPool()
        maxBlockSize = pool.maxBlockSize
        minFeeRate = pool.minFeeRate 
        
        blockSize = 0
        strandingFeeRate = float("inf")
        blockSizeLimited = 0

        txDeps = {}
        txNoDeps = []
        for txid,entry in self.mempool.iteritems():
            if not entry['depends']:
                txNoDeps.append((txid, entry['size'], entry['feeRate']))
            else:
                txDeps.update({txid: entry})

        txNoDeps.sort(key=lambda x: x[2])

        while txNoDeps:
            # We need to change this to get better stranding fr for size limited blocks. Done.
            newTx = txNoDeps.pop()
            if newTx[2] >= minFeeRate:
                if newTx[1] + blockSize <= maxBlockSize:
                    if blockSizeLimited > 0:
                        blockSizeLimited -= 1
                    else:
                        strandingFeeRate = newTx[2]
                    blockSize += newTx[1]
                    depAdded = False
                    for txid, entry in txDeps.items():
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
                    blockSizeLimited += 1
            else:
                break

        return strandingFeeRate if blockSizeLimited else minFeeRate

    # change this to use txSamples-based spacing
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






