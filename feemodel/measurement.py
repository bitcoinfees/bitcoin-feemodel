from feemodel.util import proxy, logWrite, Saveable, getBlockTimeStamp
from feemodel.config import config, saveWaitFile, saveRatesFile
from feemodel.txmempool import Block
from math import exp, cos, sin, sqrt, log, pi
from random import random, choice
from copy import deepcopy
import threading

try:
    import cPickle as pickle
except ImportError:
    import pickle

feeResolution = config['queue']['feeResolution']
priorityThresh = config['measurement']['priorityThresh']
defaultSamplingWindow = 18
defaultTxRateWindow = 2016
defaultWaitTimesWindow = 2016

ratesLock = threading.RLock()
waitLock = threading.lock()


class TxSample(object):
    def __init__(self, txid, size, feeRate):
        self.txid = txid
        self.size = size
        self.feeRate = feeRate

    def __cmp__(self, other):
        return cmp(self.feeRate, other.feeRate)

    def __repr__(self):
        return "TxSample{txid: %s, size: %d, feeRate: %d}" % (
            self.txid, self.size, self.feeRate)


class BlockTxRate(object):
    def __init__(self, block, prevBlock):
        if not prevBlock or not block.height == prevBlock.height + 1:
            raise ValueError("Blocks not consecutive.")
        newtxs = set(block.entries) - set(prevBlock.entries)
        numConflicts = len([1 for entry in block.entries.itervalues()
            if entry.get('isConflict')])
        self.numTxs = len(newtxs) - numConflicts
        self.timeInterval = block.time - prevBlock.time
        self.txSamples = [TxSample('0', block.entries[txid]['size'], block.entries[txid]['feeRate'])
            for txid in newtxs]

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class TxRates(Saveable):
    def __init__(self, samplingWindow=defaultSamplingWindow, txRateWindow=defaultTxRateWindow):
        self.blockTxRates = {}
        self.txSamples = []
        self.blockTxRatesCache = {}
        self.txSamplesCache = []
        self.samplingWindow = samplingWindow
        self.txRateWindow = txRateWindow
        self.prevBlock = None
        super(TxRates, self).__init__(saveRatesFile)

    def pushBlocks(self, blocks):
        for block in blocks:
            if block:
                try:
                    self.blockTxRates[block.height] = BlockTxRate(block, self.prevBlock)
                except ValueError:
                    pass
                else:
                    samplingThresh = block.height - self.samplingWindow
                    rateThresh = block.height - self.txRateWindow
                    for height, blockTxRate in self.blockTxRates.items():
                        if height <= samplingThresh:
                            blockTxRate.txSamples = []
                        if height <= rateThresh:
                            del self.blockTxRates[height]

                    self.txSamples = [] 
                    for height in range(block.height-self.samplingWindow+1, block.height+1):
                        blockTxRate = self.blockTxRates.get(height)
                        if blockTxRate:
                            self.txSamples.extend(blockTxRate.txSamples)

                    logWrite("TR: added block %d" % block.height)

            self.prevBlock = block

        with ratesLock:
            self.blockTxRatesCache = deepcopy(self.blockTxRates)
            self.txSamplesCache = deepcopy(self.txSamples)

    def calcRates(self, interval):
        with ratesLock:
            totalTxs = 0
            totalTime = 0
            for height in range(*interval):
                blockTxRate = self.blockTxRatesCache.get(height)
                if blockTxRate:
                    totalTxs += blockTxRate.numTxs
                    totalTime += blockTxRate.timeInterval

            if totalTxs < 0:
                # This is possible because we count entries removed as 
                # a result of mempool conflict as a negative tx rate.
                raise ValueError("Negative total txs.")

            if totalTime:
                return totalTxs / float(totalTime)
            else:
                raise ValueError("Time interval is zero.")
    
    def generateTxSample(self, expectedNumTxs):
        with ratesLock:
            k = poissonSample(expectedNumTxs)
            n = len(self.txSamplesCache)
            # may have to make this a copy.
            try:
                return [self.txSamplesCache[int(random()*n)] for i in range(k)]
            except IndexError:
                return [choice(self.txSamplesCache) for i in range(k)]

    def getByteRate(self, interval, feeRates):
        with ratesLock:
            txRate = self.calcRates(interval)
            numSamples = len(self.txSamplesCache)
            byteRates = [
                sum([tx.size for tx in self.txSamplesCache
                    if tx.feeRate >= feeRate])*txRate/numSamples
                for feeRate in feeRates
            ]

            return byteRates, txRate

    def getBestHeight(self):
        with ratesLock:
            return max(self.blockTxRatesCache) if self.blockTxRates else None

    @staticmethod
    def loadObject(saveRatesFile=saveRatesFile):
        return super(TxRates,TxRates).loadObject(saveRatesFile)

    def saveObject(self):
        with ratesLock:
            super(TxRates, self).saveObject()

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
    def __init__(self, feeClassValues, waitTimesWindow=defaultWaitTimesWindow):
        self.blockWaitTimes = {}
        self.feeClassValues = feeClassValues
        self.waitTimes = {}
        self.waitTimesWindow = waitTimesWindow
        self.waitTimesCache = {}
        self.bestHeight = None
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

            heightThresh = block.height - self.waitTimesWindow
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
        with waitLock:
            self.waitTimesCache = deepcopy(self.waitTimes)
            self.bestHeight = max(self.blockWaitTimes)

    def getBestHeight(self):
        with waitLock:
            return self.bestHeight

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

    def saveObject():
        with waitLock:
            super(TxWaitTimes, self).saveObject()


def estimateBlockInterval(interval):
    '''Estimates the block interval from blocks in range(interval[0], interval[1])'''
    numBlocks = interval[1]-interval[0]-1
    if numBlocks < 144:
        raise ValueError("Interval must be at least 144 blocks.")
    timeInterval = getBlockTimeStamp(interval[1]-1) - getBlockTimeStamp(interval[0])
    sampleMean = timeInterval / float(numBlocks)
    halfInterval = 1.96*sampleMean/numBlocks**0.5
    confInterval = (sampleMean - halfInterval, sampleMean + halfInterval)

    return sampleMean, confInterval

def poissonSample(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution#Generating_Poisson-distributed_random_variables
    if l > 30:
        return int(round(poissonApprox(l)))
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return k - 1

def poissonApprox(l):
    # box-muller
    u = random()
    v = random()

    z = sqrt(-2*log(u))*cos(2*pi*v)
    return z*sqrt(l) + l

