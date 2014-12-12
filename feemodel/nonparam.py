from feemodel.config import statsFile, historyFile, config
from random import choice

leadTimeOffset = config['pollPeriod']
numBootstrap = config['nonparam']['numBootstrap']

class NonParam(object):

    def __init__(self):
        # self.blockStats = {}
        self.feeEstimates = {}
        self.blockEstimates = {}

    def pushBlocks(self, blocks):
        for block in blocks:
            print("Pushed block " + str(block.height))

    def pushBlocks2(self, blocks):
        # Check the minLeadTime of each block
        for block in blocks:
            if not block.entries: # Empty blocks - means nothing in mempool. Don't care about them!
                continue
            try:
                minLeadTime = min([entry['leadTime'] for entry in 
                    block.entries.itervalues() if entry['inBlock']])
            except ValueError:
                minLeadTime = 0 # Change it later to use past statistics

            # self.blockStats[block.height] = BlockStat(block, minLeadTime)
            blockStats = BlockStat(block, minLeadTime)
            # self.feeEstimates[block.height] = self.blockStats[block.height].estimateFee()
            self.feeEstimates[block.height] = blockStats.estimateFee()
            self.blockEstimates[block.height] = BlockEstimate(block.size, minLeadTime)

            # for debug
            print('--------------')
            print('Blockheight: %d' % block.height)
            print(self.blockEstimates[block.height])
            print(self.feeEstimates[block.height])


class BlockStat(object):
    def __init__(self, block, minLeadTime):
        # Empty feeStats should be discarded earlier.
        self.entries = block.entries
        self.height = block.height
        self.size = block.size
        self.time = block.time
        self.minLeadTime = minLeadTime
        leadTimeThresh = self.minLeadTime + leadTimeOffset

        # In future perhaps remove high priority
        self.feeStats = [FeeStat(entry) for entry in block.entries.itervalues()
            if self.depsCheck(entry)
            and entry['leadTime'] >= leadTimeThresh
            and entry['feeRate']]
        self.feeStats.sort(key=lambda x: x.feeRate, reverse=True)

    def estimateFee(self):
        minFeeRate = BlockStat.calcMinFeeRateSingle(self.feeStats)
        
        aboveList = filter(lambda x: x.feeRate >= minFeeRate, self.feeStats)
        belowList = filter(lambda x: x.feeRate < minFeeRate, self.feeStats)

        kAbove = sum([feeStat.inBlock for feeStat in aboveList])
        kBelow = sum([not feeStat.inBlock for feeStat in belowList])

        nAbove = len(aboveList)
        nBelow = len(belowList)

        altBiasRef = belowList[0].feeRate if nBelow else 0

        bootstrap = [BlockStat.calcMinFeeRateSingle(self.bootstrapSample()) 
            for i in range(numBootstrap)]

        mean = float(sum(bootstrap)) / len(bootstrap)
        std = (sum([(b-mean)**2 for b in bootstrap]) / (len(bootstrap)-1))**0.5

        biasRef = max((minFeeRate, abs(mean-minFeeRate)), 
            (altBiasRef, abs(mean-altBiasRef)), key=lambda x: x[1])[0]
        bias = mean - biasRef

        return FeeEstimate(minFeeRate, bias, std, (kAbove,nAbove), (kBelow,nBelow))

    def bootstrapSample(self):
        sample = [choice(self.feeStats) for i in range(len(self.feeStats))]
        sample.sort(key=lambda x: x.feeRate, reverse=True)

        return sample


    def depsCheck(self, entry):
        deps = [self.entries[depId] for depId in entry['depends']]
        return all([dep['inBlock'] for dep in deps])

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
        argmaxk = [feeStat.feeRate for feeStat in feeStats if kvals[feeStat.feeRate] == maxk]

        return min(argmaxk)


class FeeEstimate(object):
    def __init__(self, minFeeRate, bias, std, abovekn, belowkn):
        self.minFeeRate = minFeeRate
        self.bias = bias
        self.std = std
        self.abovekn = abovekn
        self.belowkn = belowkn

    def __repr__(self):
        return "FeeEstimate{minFeeRate: %d, bias: %d, std: %d, above: %s, below: %s}" % (
            self.minFeeRate, self.bias, self.std, self.abovekn, self.belowkn)

class BlockEstimate(object):
    def __init__(self, size, minLeadTime):
        self.size = size
        self.minLeadTime = minLeadTime

    def __repr__(self):
        return "BlockEstimate{size: %d, minLeadTime: %.1f}" % (self.size, self.minLeadTime)


class FeeStat(object):
    def __init__(self, entry):
        self.feeRate = entry['feeRate']
        self.priority = entry['currentpriority']
        self.size =  entry['size']
        self.inBlock = entry['inBlock']