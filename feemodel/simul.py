from feemodel.measurement import TxRates, TxSample
from feemodel.pools import PoolEstimator
from feemodel.util import proxy
from feemodel.queue import QEstimator
from feemodel.config import config
from bitcoin.core import COIN
from collections import defaultdict
from random import expovariate
from copy import deepcopy, copy
from bisect import insort

# blockRate = config['simul']['blockRate'] # Once every 10 minutes
blockRate = 1./600
rateRatioThresh = 0.9
convergeThresh = 0.0001

class Simul(object):
    def __init__(self):
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
        self.initMempool(mempool)
        waitTimes = {feeRate: [] for feeRate in self.feeClassValues}
        txNoDeps = self.txNoDeps
        txDeps = self.txDeps

        for i in range(1000):
            stranded = self.feeClassValues[:]
            self.txNoDeps = txNoDeps[:]
            self.txDeps = {txid: {'tx': txDeps[txid]['tx'], 'depends': txDeps[txid]['depends'][:]}
                for txid in txDeps}
            totaltime = 0.
            while stranded:
                t = self.addToMempool()
                sfr = self.processBlock()
                totaltime += t
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
        self.initMempool(mempool)

        q = QEstimator(self.feeClassValues)
        convergeCount = 0
        for i in range(10000):
            t = self.addToMempool()
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

    def addToMempool(self):
        t = expovariate(blockRate)
        txSample = self.tr.generateTxSample(t*self.txRate)
        self.txNoDeps.extend([tx for tx in txSample if tx.feeRate >= self.stableFeeRate])
        self.txNoDeps.sort(key=lambda x: x.feeRate)

        return t

    def initMempool(self, mempool):
        self.txNoDeps = []
        self.txDeps = {}
        self.depMap = defaultdict(list)

        for txid, entry in mempool.items():
            if not 'feeRate' in entry:
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']
            if not entry['depends']:
                self.txNoDeps.append(TxSample(txid, entry['size'], entry['feeRate']))
            else:
                for dep in entry['depends']:
                    self.depMap[dep].append(txid)
                self.txDeps[txid] = {'tx': TxSample(txid, entry['size'], entry['feeRate']),
                    'depends': entry['depends']}

        self.txNoDeps.sort(key=lambda x: x.feeRate)

    def processBlock(self):
        maxBlockSize, minFeeRate = self.pe.selectRandomPool()

        blockSize = 0
        strandingFeeRate = float("inf")
        blockSizeLimited = 0

        rejectedTx = []
        while self.txNoDeps:
            # We need to change this to get better stranding fr for size limited blocks. Done.
            newTx = self.txNoDeps.pop()
            if newTx.feeRate >= minFeeRate:
                if newTx.size + blockSize <= maxBlockSize:
                    if blockSizeLimited > 0:
                        blockSizeLimited -= 1
                    else:
                        strandingFeeRate = min(newTx.feeRate, strandingFeeRate)

                    blockSize += newTx.size

                    dependants = self.depMap.get(newTx.txid)
                    if dependants:
                        for txid in dependants:
                            entry = self.txDeps[txid]
                            entry['depends'].remove(newTx.txid)
                            if not entry['depends']:
                                insort(self.txNoDeps, entry['tx'])
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






