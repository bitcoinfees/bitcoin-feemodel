import threading
from feemodel.model import InsufficientDataError
from feemodel.config import statsFile, historyFile, config
from feemodel.util import logWrite, proxy
from feemodel.txmempool import Block
from random import choice
from math import ceil

numBootstrap = config['nonparam']['numBootstrap']
numBlocksUsed = config['nonparam']['numBlocksUsed']
maxBlockAge = config['nonparam']['maxBlockAge']
sigLevel = config['nonparam']['sigLevel']
minP = config['nonparam']['minP']

class NonParam(object):

    _noBootstrap = None

    def __init__(self, autoLoad=False, blockHeightRange=None, noBootstrap=False):
        self.aboveBelowProb = (None, None)
        self.blockEstimates = {}
        self.zeroInBlock = []
        self.lock = threading.Lock()
        NonParam._noBootstrap = noBootstrap

        if autoLoad:
            currHeight = proxy.getblockcount()
            for height in range(currHeight, currHeight-maxBlockAge, -1):
                block = [Block.blockFromHistory(height)]
                self.pushBlocks(block)
                if len(self.blockEstimates) >= numBlocksUsed[1]:
                    break
        elif blockHeightRange:
            for height in range(*blockHeightRange):
                block = [Block.blockFromHistory(height)]
                self.pushBlocks(block)

    def estimateFee(self, nBlocks):
        if nBlocks <= 0:
            raise ValueError("nBlocks must be greater than 0.")

        if len(self.blockEstimates) >= numBlocksUsed[0]:
            p = 1 - (1-sigLevel)**(1./nBlocks)
            p = min(max(minP, p), 1)
            minFeeRates = [blockEstimate.feeEstimate.minFeeRate 
                for blockEstimate in self.blockEstimates.itervalues()]
            minFeeRates.sort()
            idx = int(ceil(p*len(minFeeRates)))
            idx = max(1, idx)
            return minFeeRates[idx-1]
        else:
            raise InsufficientDataError("Need at least " + str(numBlocksUsed[0])
                + " blocks of data.")

    def estimateTx(self, entry):
        pass



    def pushBlocks(self, blocks):
        assert not self.lock.locked()
        # This is non-blocking: we just want to assert that this method is not 
        # accessed from multiple threads.
        self.lock.acquire(False)

        for block in blocks:
            if not block or not block.entries or block.height in self.blockEstimates:
            # Empty block.entries - means empty mempool. Discard it!
                continue
            try:
                minLeadTime = min([entry['leadTime'] for entry in 
                    block.entries.itervalues() if entry['inBlock']])
            except ValueError:
                self.zeroInBlock.append(block)
                continue

            self._addBlockEstimate(block,minLeadTime)

        if self.zeroInBlock and len(self.blockEstimates) >= numBlocksUsed[0]:            
            minLeadTimes = [b.minLeadTime for b in self.blockEstimates.values()]
            defaultMLT = minLeadTimes[9*len(minLeadTimes)//10 - 1] # 90th percentile
            for block in self.zeroInBlock:
                self._addBlockEstimate(block,defaultMLT)
            self.zeroInBlock = []

        self.lock.release()

    def _addBlockEstimate(self,block,minLeadTime):
        blockStats = BlockStat(block, minLeadTime)
        feeEstimate = blockStats.estimateFee()
        if feeEstimate:
            self.blockEstimates[block.height] = BlockEstimate(
                block.size, minLeadTime, feeEstimate)
            logWrite('Model: added block ' + str(block.height) + ', %s' %
                self.blockEstimates[block.height])

        # Clean up old blockEstimates
        blockThresh = block.height - numBlocksUsed[1]
        if blockThresh < block.height:
            keysToDelete = [key for key in self.blockEstimates if key <= blockThresh]
            for key in keysToDelete:
                del self.blockEstimates[key]

        # recompute other stats
        sumTuplesFn = lambda x,y: (x[0]+y[0],x[1]+y[1])
        aboveknTotal = reduce(sumTuplesFn, [blockEstimate.feeEstimate.abovekn
            for blockEstimate in self.blockEstimates.itervalues()], (0,0))
        belowknTotal = reduce(sumTuplesFn, [blockEstimate.feeEstimate.belowkn
            for blockEstimate in self.blockEstimates.itervalues()], (0,0))

        aboveRatio = aboveknTotal[0]/float(aboveknTotal[1]) if aboveknTotal[1] else None
        belowRatio = belowknTotal[0]/float(belowknTotal[1]) if belowknTotal[1] else None
        self.aboveBelowProb = (aboveRatio,belowRatio)

    def __eq__(self,other):
        if not isinstance(other,NonParam):
            return False
        return self.blockEstimates == other.blockEstimates and self.zeroInBlock == other.zeroInBlock


class BlockStat(object):
    def __init__(self, block, minLeadTime):
        self.entries = block.entries
        self.height = block.height
        self.size = block.size
        self.time = block.time
        self.minLeadTime = minLeadTime

        # In future perhaps remove high priority
        self.feeStats = [FeeStat(entry) for entry in block.entries.itervalues()
            if self._depsCheck(entry)
            and entry['leadTime'] >= self.minLeadTime
            and entry['feeRate']]
        self.feeStats.sort(key=lambda x: x.feeRate, reverse=True)

    def estimateFee(self):
        if not self.feeStats:
            # No txs which pass the filtering
            return None

        minFeeRate = BlockStat.calcMinFeeRateSingle(self.feeStats)
        
        aboveList = filter(lambda x: x.feeRate >= minFeeRate, self.feeStats)
        belowList = filter(lambda x: x.feeRate < minFeeRate, self.feeStats)

        kAbove = sum([feeStat.inBlock for feeStat in aboveList])
        kBelow = sum([not feeStat.inBlock for feeStat in belowList])

        nAbove = len(aboveList)
        nBelow = len(belowList)

        if minFeeRate != float("inf") and not NonParam._noBootstrap:
            altBiasRef = belowList[0].feeRate if nBelow else 0

            bootstrap = [BlockStat.calcMinFeeRateSingle(self.bootstrapSample()) 
                for i in range(numBootstrap)]

            mean = float(sum(bootstrap)) / len(bootstrap)
            std = (sum([(b-mean)**2 for b in bootstrap]) / (len(bootstrap)-1))**0.5

            biasRef = max((minFeeRate, abs(mean-minFeeRate)), 
                (altBiasRef, abs(mean-altBiasRef)), key=lambda x: x[1])[0]
            bias = mean - biasRef
        else:
            bias = float("inf")
            std = float("inf")

        threshFeeStats = aboveList[-10:] + belowList[:10]

        return FeeEstimate(minFeeRate, bias, std, (kAbove,nAbove), (kBelow,nBelow), threshFeeStats)

    def bootstrapSample(self):
        sample = [choice(self.feeStats) for i in range(len(self.feeStats))]
        sample.sort(key=lambda x: x.feeRate, reverse=True)

        return sample


    def _depsCheck(self, entry):
        deps = [self.entries.get(depId) for depId in entry['depends']]
        return all([dep['inBlock'] if dep else False for dep in deps])

    @staticmethod
    def calcMinFeeRateSingle(feeStats):
        # feeStats should be sorted by fee rate, reverse=True
        # To-do: Handle empty list (or maybe should be checked earlier)

        kvals = {float("inf"): 0}
        feeRateCurr = float("inf")

        for feeStat in feeStats:
            if feeStat.feeRate < feeRateCurr:
                kvals[feeStat.feeRate] = kvals[feeRateCurr]
                feeRateCurr = feeStat.feeRate

            kvals[feeRateCurr] += 1 if feeStat.inBlock else -1

        maxk = max(kvals.itervalues())
        argmaxk = [feeRate for feeRate in kvals.iterkeys() if kvals[feeRate] == maxk]

        return max(argmaxk)


class FeeEstimate(object):
    def __init__(self, minFeeRate, bias, std, abovekn, belowkn, threshFeeStats):
        self.minFeeRate = minFeeRate
        self.bias = bias
        self.std = std
        self.abovekn = abovekn
        self.belowkn = belowkn
        self.threshFeeStats = threshFeeStats
        self.rmse = (bias**2 + std**2)**0.5

    def __repr__(self):
        return "FE{mfr: %.1f, bias: %.1f, std: %.1f, above: %s, below: %s}" % (
            self.minFeeRate, self.bias, self.std, self.abovekn, self.belowkn)

class BlockEstimate(object):
    def __init__(self, size, minLeadTime, feeEstimate):
        self.size = size
        self.minLeadTime = minLeadTime
        self.feeEstimate = feeEstimate

    def __repr__(self):
        return "BE{size: %d, mlt: %.1f, %s}" % (self.size, self.minLeadTime, self.feeEstimate)


class FeeStat(object):
    def __init__(self, entry):
        self.feeRate = entry['feeRate']
        self.priority = entry['currentpriority']
        self.size =  entry['size']
        self.inBlock = entry['inBlock']

    def __repr__(self):
        return "FeeStat(%d,%d)" % (self.feeRate,self.inBlock)