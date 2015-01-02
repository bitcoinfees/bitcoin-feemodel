from feemodel.measurement import TxRates
from feemodel.pools import PoolEstimator
from feemodel.util import proxy
from feemodel.queue import QEstimator
from feemodel.config import config
from bitcoin.core import COIN
from collections import defaultdict
from random import expovariate
from copy import deepcopy

# blockRate = config['simul']['blockRate'] # Once every 10 minutes
blockRate = 1./600
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
        mempool, txNoDeps, depMap = self.initMempool(mempool)
        waitTimes = {feeRate: [] for feeRate in self.feeClassValues}

        for i in range(1000):
            stranded = self.feeClassValues[:]
            self.mempool = deepcopy(mempool)
            self.txNoDeps = deepcopy(txNoDeps)
            self.depMap = deepcopy(depMap)
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

        self.mempool, self.txNoDeps, self.depMap = self.initMempool(self.mempool)

        q = QEstimator(self.feeClassValues)
        convergeCount = 0
        for i in range(10000):
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
        self.txNoDeps.extend([
            (
                '%d_%d' % (blockIdx, tidx),
                tx['size'],
                tx['feeRate']
            )
            for tidx, tx in enumerate(txSample)
            if tx['feeRate'] >= self.stableFeeRate
        ])
        self.txNoDeps.sort(key=lambda x: x[2])

        return t

    def initMempool(self, mempool):
        txNoDeps = []
        depMap = defaultdict(list)

        for txid, entry in mempool.items():
            if not 'feeRate' in entry:
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']
            if not entry['depends']:
                txNoDeps.append((txid, entry['size'], entry['feeRate']))
                del mempool[txid]
            else:
                for dep in entry['depends']:
                    depMap[dep].append(txid)

        txNoDeps.sort(key=lambda x: x[2])

        return mempool, txNoDeps, depMap

    def processBlock(self):
        name, pool = self.pe.selectRandomPool()
        maxBlockSize = pool.maxBlockSize
        minFeeRate = pool.minFeeRate 
        
        blockSize = 0
        strandingFeeRate = float("inf")
        blockSizeLimited = 0

        rejectedTx = []
        while self.txNoDeps:
            # We need to change this to get better stranding fr for size limited blocks. Done.
            newTx = self.txNoDeps.pop()
            if newTx[2] >= minFeeRate:
                if newTx[1] + blockSize <= maxBlockSize:
                    if blockSizeLimited > 0:
                        blockSizeLimited -= 1
                    else:
                        strandingFeeRate = newTx[2]
                    blockSize += newTx[1]
                    depAdded = False

                    dependants = self.depMap.get(newTx[0])
                    if dependants:
                        for txid in dependants:
                            entry = self.mempool[txid]
                            entry['depends'].remove(newTx[0])
                            if not entry['depends']:
                                self.txNoDeps.append((txid, entry['size'], entry['feeRate']))
                                del self.mempool[txid]
                                depAdded = True
                        del self.depMap[newTx[0]]
                    if depAdded:
                        self.txNoDeps.sort(key=lambda x: x[2])
                else:
                    rejectedTx.append(newTx)
                    blockSizeLimited += 1
            else:
                rejectedTx.append(newTx)
                break

        self.txNoDeps.extend(rejectedTx)
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






