from feemodel.util import proxy, logWrite, Saveable
from feemodel.config import config, saveWaitFile, saveRatesFile
from feemodel.txmempool import Block
from math import exp
from random import random, choice
from copy import deepcopy
import threading
from numpy.random import poisson

try:
    import cPickle as pickle
except ImportError:
    import pickle

feeResolution = config['queue']['feeResolution']
priorityThresh = config['measurement']['priorityThresh']
samplingWindow = 18
rateWindow = 2016
waitTimesWindow = 2016

ratesLock = threading.Lock()

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
    def __init__(self, samplingWindow=samplingWindow, rateWindow=rateWindow):
        self.blockRates = {}
        self.txSamples = []
        self.blockRatesCache = {}
        self.txSamplesCache = []
        self.samplingWindow = samplingWindow
        self.rateWindow = rateWindow
        self.prevBlock = None
        super(TxRates, self).__init__(saveRatesFile)

    def pushBlocks(self, blocks):
        for block in blocks:
            if block:
                try:
                    self.blockRates[block.height] = BlockTxRate(block, self.prevBlock)
                except ValueError:
                    pass
                else:
                    samplingThresh = block.height - self.samplingWindow
                    rateThresh = block.height - self.rateWindow
                    for height, blockRate in self.blockRates.items():
                        if height <= samplingThresh:
                            blockRate.txSamples = []
                        if height <= rateThresh:
                            del self.blockRates[height]

                    self.txSamples = [] 
                    for height in range(block.height-self.samplingWindow+1, block.height+1):
                        blockRate = self.blockRates.get(height)
                        if blockRate:
                            self.txSamples.extend(blockRate.txSamples)

                    logWrite("TR: added block %d" % block.height)

            self.prevBlock = block

        with ratesLock:
            self.blockRatesCache = deepcopy(self.blockRates)
            self.txSamplesCache = deepcopy(self.txSamples)

    def calcRates(self, interval):
        with ratesLock:
            totalTxs = 0
            totalTime = 0
            for height in range(*interval):
                blockRate = self.blockRatesCache.get(height)
                if blockRate:
                    totalTxs += blockRate.numTxs
                    totalTime += blockRate.timeInterval

            if totalTime:
                return totalTxs / float(totalTime)
            else:
                raise ValueError("Time interval is zero.")
    
    def generateTxSample(self, expectedNumTxs):
        with ratesLock:
            # k = poissonSample(expectedNumTxs)
            # k = int(expectedNumTxs)
            k = poisson(expectedNumTxs)
            return [choice(self.txSamplesCache) for i in xrange(k)]

    @staticmethod
    def loadObject():
        return super(TxRates,TxRates).loadObject(saveRatesFile)

    def __eq__(self,other):
        return self.__dict__ == other.__dict__


class BlockTxWaitTimes(object):
    def __init__(self, feeClassValues):
        self.avgWaitTimes = {feeRate: (0, 0.) for feeRate in feeClassValues}

    def addTx(self, feeRate, waitTime):
        feeClass = None
        for feeClassValue in self.avgWaitTimes:
            if feeClassValue <= feeRate:
                feeClass = max(feeClass, feeClassValue)

        if feeClass is not None:
            numTxs, prevWaitTime = self.avgWaitTimes[feeClass]
            self.avgWaitTimes[feeClass] = (numTxs+1,
                (prevWaitTime*numTxs + waitTime) / (numTxs + 1))


class TxWaitTimes(Saveable):
    def __init__(self, feeClassValues):
        self.blockWaitTimes = {}
        self.feeClassValues = feeClassValues
        self.waitTimes = {}
        super(TxWaitTimes, self).__init__(saveWaitFile)
        
    def pushBlocks(self, blocks):
        for block in blocks:
            if not block:
                continue
            self.blockWaitTimes[block.height] = BlockTxWaitTimes(self.feeClassValues)
            for entry in block.entries.itervalues():
                if self._countTx(entry):
                    self.blockWaitTimes[block.height].addTx(
                        entry['feeRate'], block.time - entry['time'])
            logWrite("WT: Added block %d" % block.height)

            heightThresh = block.height - waitTimesWindow
            for height in self.blockWaitTimes.keys():
                if height <= heightThresh:
                    del self.blockWaitTimes[height]
            
            self.calcWaitTimes()

    def calcWaitTimes(self):
        if not len(self.blockWaitTimes):
            raise ValueError("WT: No valid blocks.")
        self.waitTimes = {}
        for feeClassValue in self.feeClassValues:
            totalTxs = sum([wtBlock.avgWaitTimes[feeClassValue][0]
                for wtBlock in self.blockWaitTimes.itervalues()])
            if totalTxs:
                self.waitTimes[feeClassValue] = (
                    sum
                    ([
                        wtBlock.avgWaitTimes[feeClassValue][0]*
                        wtBlock.avgWaitTimes[feeClassValue][1]
                        for wtBlock in self.blockWaitTimes.itervalues()
                    ]) / totalTxs,
                    totalTxs
                )
            else:
                self.waitTimes[feeClassValue] = (-1, totalTxs)

    @staticmethod
    def _countTx(entry):
        return (
            entry['inBlock'] and
            not entry['depends'] and
            entry['currentpriority'] < priorityThresh
        )

    @staticmethod
    def loadObject():
        return super(TxWaitTimes,TxWaitTimes).loadObject(saveWaitFile)

def poissonSample(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution#Generating_Poisson-distributed_random_variables
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return k - 1






# class WaitMeasure(object):
#     def __init__(self, maxMFR, adaptive, loadFile=saveWaitFile):
#         self.adaptive = adaptive
#         self.blockData = {}
#         self.maxMFR = maxMFR
#         self.waitTimes = None
#         self.bestHeight = None

#         if loadFile:
#             try:
#                 self.loadBlockData()
#             except IOError:
#                 logWrite("WM: Couldn't load saved measurements; loading from disk.")
#             else:
#                 logWrite("WM: Saved measurements loaded: %s" % (repr(self),))

#     def getStats(self):
#         if self.waitTimes:
#             return [repr(self)] + [(idx*feeResolution,) + t
#                 for idx,t in enumerate(self.waitTimes)]
#         else:
#             return []

#     def pushBlocks(self, blocks, isInit=False):
#         for block in blocks:
#             if not block:
#                 continue
#             self.blockData[block.height] = WaitMeasureBlock(self.maxMFR)
#             self.bestHeight = block.height
#             numTxs = 0
#             for entry in block.entries.itervalues():
#                 if (entry['inBlock']
#                     and entry['feeRate'] < (self.maxMFR + feeResolution)
#                     and not entry['depends']
#                     and entry['currentpriority'] < priorityThresh):
#                         self.blockData[block.height].addTx(
#                             entry['feeRate'], block.time - entry['time'])
#                         numTxs += 1
#             logWrite("WM: Added block %d with %d transactions" %
#                         (block.height, numTxs))
#         if not isInit:
#             self.adaptiveCalc()
#             try:
#                 self.saveBlockData()
#             except IOError:
#                 logWrite("WM: Couldn't save block data.")

#     def adaptiveCalc(self):
#         if not self.bestHeight:
#             raise ValueError("WM: Empty block data.")

#         heightThresh = self.bestHeight - self.adaptive
#         for height in self.blockData.keys():
#             if height < heightThresh:
#                 del self.blockData[height]

#         self.waitTimes = [None] * (self.maxMFR // feeResolution + 1)
#         for idx in range(self.maxMFR // feeResolution + 1):
#             totalTxs = sum([wmBlock.avgWaitTimes[idx][0]
#                 for wmBlock in self.blockData.itervalues()])
#             if totalTxs:
#                 self.waitTimes[idx] = (sum([wmBlock.avgWaitTimes[idx][0]*wmBlock.avgWaitTimes[idx][1]
#                     for wmBlock in self.blockData.itervalues()]) / totalTxs, totalTxs)
#             else:
#                 self.waitTimes[idx] = (-1, totalTxs)

#         # self.waitTimes = [
#         #     sum([wmBlock.avgWaitTimes[idx][0]*wmBlock.avgWaitTimes[idx][1]
#         #         for wmBlock in self.blockData.itervalues()]) /
#         #     sum([wmBlock.avgWaitTimes[idx][0]
#         #         for wmBlock in self.blockData.itervalues()])
#         #     for idx in range(self.maxMFR // feeResolution + 1)
#         # ]

#     def saveBlockData(self, dbFile=saveWaitFile):
#         with open(dbFile, 'wb') as f:
#             pickle.dump(self.blockData,f)

#     def loadBlockData(self, dbFile=saveWaitFile):
#         with open(dbFile, 'rb') as f:
#             self.blockData = pickle.load(f)
#         try:
#             self.bestHeight = max(self.blockData)
#             self.maxMFR = (len(self.blockData[self.bestHeight].avgWaitTimes)-1)*feeResolution
#         except ValueError:
#             self.bestHeight = None

#     def __repr__(self):
#         return "WM{maxMFR: %d, adaptive: %d, bestHeight: %d, numBlocks: %d}" % (
#             self.maxMFR, self.adaptive, self.bestHeight if self.bestHeight else -1, len(self.blockData))


# class WaitMeasureBlock(object):
#     def __init__(self, maxMFR):
#         self.avgWaitTimes = [(0, 0.)]*(maxMFR // feeResolution + 1)

#     def addTx(self, feeRate, waitTime):
#         feeClassIdx = feeRate // feeResolution
#         numTxs, prevWaitTime = self.avgWaitTimes[feeClassIdx]
#         self.avgWaitTimes[feeClassIdx] = (numTxs+1,
#             (prevWaitTime*numTxs + waitTime) / (numTxs + 1))