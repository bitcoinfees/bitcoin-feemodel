from feemodel.util import proxy, logWrite, Saveable
from feemodel.config import config, saveWaitFile, saveRatesFile
from feemodel.txmempool import Block
from math import exp
from random import random, choice

try:
    import cPickle as pickle
except ImportError:
    import pickle

feeResolution = config['queue']['feeResolution']
priorityThresh = config['measurement']['priorityThresh']
samplingInterval = 18
rateInterval = 2016

class BlockTxRate(object):
    def __init__(self, block, prevBlock):
        if not prevBlock or not block.height == prevBlock.height + 1:
            raise ValueError("Blocks not consecutive.")
        newtxs = set(block.entries) - set(prevBlock.entries)
        self.numTxs = len(newtxs)
        self.timeInterval = block.time - prevBlock.time
        self.txSamples = [block.entries[txid] for txid in newtxs]
        for tx in self.txSamples:
            tx['depends'] = []

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

class TxRates(Saveable):
    def __init__(self, samplingInterval=samplingInterval, rateInterval=rateInterval):
        self.blockRates = {}
        self.txSamples = []
        self.samplingInterval = samplingInterval
        self.rateInterval = rateInterval
        self.prevBlock = None
        super(TxRates, self).__init__(saveRatesFile)

    def pushBlocks(self, blocks):
        for block in blocks:
            if block:
                try:
                    self.blockRates[block.height] = BlockTxRate(block, self.prevBlock)
                except ValueError:
                    pass
                for height, blockRate in self.blockRates.items():
                    samplingThresh = block.height - self.samplingInterval
                    rateThresh = block.height - self.rateInterval
                    if height <= samplingThresh:
                        blockRate.txSamples = []
                    if height <= rateThresh:
                        del self.blockRates[height]

                self.txSamples = [] 
                for height in range(block.height-self.samplingInterval+1, block.height+1):
                    blockRate = self.blockRates.get(height)
                    if blockRate:
                        self.txSamples.extend(blockRate.txSamples)

                logWrite("TR: added block %d" % block.height)

            self.prevBlock = block

    def calcRates(self, interval):
        totalTxs = 0
        totalTime = 0
        for height in range(*interval):
            blockRate = self.blockRates.get(height)
            if blockRate:
                totalTxs += blockRate.numTxs
                totalTime += blockRate.timeInterval

        if totalTime:
            return totalTxs / float(totalTime)
        else:
            raise ValueError("Time interval is zero.")
    
    def generateTxSample(self, expectedNumTxs):
        k = poissonSample(expectedNumTxs)
        return [choice(self.txSamples) for i in xrange(k)]

    @staticmethod
    def loadObject():
        return super(TxRates,TxRates).loadObject(saveRatesFile)

    def __eq__(self,other):
        return self.__dict__ == other.__dict__



    # def calcRates(self, blockHeightRange):
    #     self.txSamples = {}
    #     prevBlock = None
    #     for height in range(*blockHeightRange):
    #         block = Block.blockFromHistory(height)
    #         if block and prevBlock:
    #             newtxs = set(block.entries) - set(prevBlock.entries)
    #             self.txSamples.update({
    #                 txid: block.entries[txid]
    #                 for txid in newtxs
    #             })
    #             for txid, entry in block.entries.iteritems():
    #                 if entry.get('isConflict') and txid in self.txSamples:
    #                     del self.txSamples[txid]
    #             self.totalTime += block.time - prevBlock.time

    #         prevBlock = block

    #     if self.totalTime:
    #         self.txRate = len(self.txSamples) / float(self.totalTime)
    #     else:
            # logWrite("TxRates error: measurement interval is zero.")



def poissonSample(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution#Generating_Poisson-distributed_random_variables
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return k - 1


class WaitMeasure(object):
    def __init__(self, maxMFR, adaptive, loadFile=saveWaitFile):
        self.adaptive = adaptive
        self.blockData = {}
        self.maxMFR = maxMFR
        self.waitTimes = None
        self.bestHeight = None

        if loadFile:
            try:
                self.loadBlockData()
            except IOError:
                logWrite("WM: Couldn't load saved measurements; loading from disk.")
            else:
                logWrite("WM: Saved measurements loaded: %s" % (repr(self),))

    def getStats(self):
        if self.waitTimes:
            return [repr(self)] + [(idx*feeResolution,) + t
                for idx,t in enumerate(self.waitTimes)]
        else:
            return []

    def pushBlocks(self, blocks, isInit=False):
        for block in blocks:
            if not block:
                continue
            self.blockData[block.height] = WaitMeasureBlock(self.maxMFR)
            self.bestHeight = block.height
            numTxs = 0
            for entry in block.entries.itervalues():
                if (entry['inBlock']
                    and entry['feeRate'] < (self.maxMFR + feeResolution)
                    and not entry['depends']
                    and entry['currentpriority'] < priorityThresh):
                        self.blockData[block.height].addTx(
                            entry['feeRate'], block.time - entry['time'])
                        numTxs += 1
            logWrite("WM: Added block %d with %d transactions" %
                        (block.height, numTxs))
        if not isInit:
            self.adaptiveCalc()
            try:
                self.saveBlockData()
            except IOError:
                logWrite("WM: Couldn't save block data.")

    def adaptiveCalc(self):
        if not self.bestHeight:
            raise ValueError("WM: Empty block data.")

        heightThresh = self.bestHeight - self.adaptive
        for height in self.blockData.keys():
            if height < heightThresh:
                del self.blockData[height]

        self.waitTimes = [None] * (self.maxMFR // feeResolution + 1)
        for idx in range(self.maxMFR // feeResolution + 1):
            totalTxs = sum([wmBlock.avgWaitTimes[idx][0]
                for wmBlock in self.blockData.itervalues()])
            if totalTxs:
                self.waitTimes[idx] = (sum([wmBlock.avgWaitTimes[idx][0]*wmBlock.avgWaitTimes[idx][1]
                    for wmBlock in self.blockData.itervalues()]) / totalTxs, totalTxs)
            else:
                self.waitTimes[idx] = (-1, totalTxs)

        # self.waitTimes = [
        #     sum([wmBlock.avgWaitTimes[idx][0]*wmBlock.avgWaitTimes[idx][1]
        #         for wmBlock in self.blockData.itervalues()]) /
        #     sum([wmBlock.avgWaitTimes[idx][0]
        #         for wmBlock in self.blockData.itervalues()])
        #     for idx in range(self.maxMFR // feeResolution + 1)
        # ]

    def saveBlockData(self, dbFile=saveWaitFile):
        with open(dbFile, 'wb') as f:
            pickle.dump(self.blockData,f)

    def loadBlockData(self, dbFile=saveWaitFile):
        with open(dbFile, 'rb') as f:
            self.blockData = pickle.load(f)
        try:
            self.bestHeight = max(self.blockData)
            self.maxMFR = (len(self.blockData[self.bestHeight].avgWaitTimes)-1)*feeResolution
        except ValueError:
            self.bestHeight = None

    def __repr__(self):
        return "WM{maxMFR: %d, adaptive: %d, bestHeight: %d, numBlocks: %d}" % (
            self.maxMFR, self.adaptive, self.bestHeight if self.bestHeight else -1, len(self.blockData))


class WaitMeasureBlock(object):
    def __init__(self, maxMFR):
        self.avgWaitTimes = [(0, 0.)]*(maxMFR // feeResolution + 1)

    def addTx(self, feeRate, waitTime):
        feeClassIdx = feeRate // feeResolution
        numTxs, prevWaitTime = self.avgWaitTimes[feeClassIdx]
        self.avgWaitTimes[feeClassIdx] = (numTxs+1,
            (prevWaitTime*numTxs + waitTime) / (numTxs + 1))