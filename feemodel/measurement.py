from feemodel.util import proxy, logWrite, Saveable, getBlockTimeStamp, pickle
from feemodel.config import config, saveWaitFile, saveRatesFile, historyFile
from feemodel.txmempool import Block
from math import exp, cos, sin, sqrt, log, pi
from random import random, choice, sample
from copy import deepcopy
import threading

feeResolution = config['queue']['feeResolution']
priorityThresh = config['measurement']['priorityThresh']
defaultMaxSamples = 10000
defaultMinRateTime = 3600 # 1 hour
minWaitBlocks = 12
defaultWaitTimesWindow = 2016

waitLock = threading.Lock()


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


class TxRates(Saveable):
    # Need to make stoppable.
    def __init__(self, maxSamples=defaultMaxSamples, minRateTime=defaultMinRateTime,
            saveRatesFile=saveRatesFile):
        self.resetParams()
        self.maxSamples = maxSamples
        self.minRateTime = minRateTime
        super(TxRates, self).__init__(saveRatesFile)

    def resetParams(self):
        self.txSamples = []
        self.txRate = None
        self.bestHeight = 0
        self.totalTime = 0.
        self.totalTxs = 0

    def calcRates(self, blockHeightRange, dbFile=historyFile, stopFlag=None):
        self.resetParams()
        prevBlock = None
        for height in range(*blockHeightRange):
            if stopFlag and stopFlag.is_set():
                raise ValueError("calcRates terminated.")
            block = Block.blockFromHistory(height, dbFile)
            self.addBlock(block, prevBlock)
            prevBlock = block
            if block:
                self.bestHeight = height

        if self.totalTime < self.minRateTime:
            raise ValueError("Time elapsed must be greater than %ds" % self.minRateTime)
        if self.totalTxs < 0:
            raise ValueError("Negative total txs.")
        self.txRate = self.totalTxs / self.totalTime
        for tx in self.txSamples:
            tx.txid = tx.txid + '_'


    def addBlock(self, block, prevBlock):
        if not block or not prevBlock or block.height != prevBlock.height + 1:
            return
        newtxs = set(block.entries) - set(prevBlock.entries)
        newTxSample = [TxSample(txid, block.entries[txid]['size'], block.entries[txid]['feeRate'])
            for txid in newtxs]
        newTotalTxs = self.totalTxs + len(newtxs)
        oldProp = float(self.totalTxs) / newTotalTxs
        combinedSize = min(self.maxSamples, len(self.txSamples)+len(newtxs))
        numKeepOld = int(round(oldProp*combinedSize))
        if numKeepOld > len(self.txSamples):
            numKeepOld = len(self.txSamples)
            numAddNew = int(round(numKeepOld/oldProp*(1-oldProp)))
        elif combinedSize - numKeepOld > len(newtxs):
            numAddNew = len(newtxs)
            numKeepOld = int(round(numAddNew/(1-oldProp)*oldProp))
        else:
            numAddNew = combinedSize - numKeepOld

        combinedSample = sample(self.txSamples, numKeepOld) + sample(newTxSample, numAddNew)
        self.totalTxs = newTotalTxs

        conflicts = [txid for txid, entry in block.entries.iteritems() if entry.get('isConflict')]
        self.txSamples = filter(lambda x: x.txid not in conflicts, combinedSample)
        # You should only subtract the conflicts if time interval is not zero.
        # Done.
        interBlockTime = block.time - prevBlock.time
        self.totalTime += interBlockTime
        if interBlockTime:
            self.totalTxs -= len(conflicts)

    def getByteRate(self, feeRates):
        if not self.txRate:
            raise ValueError("Need to run calcRates first.")
        n = len(self.txSamples)
        byteRates = [
            sum([tx.size for tx in self.txSamples
                if tx.feeRate >= feeRate])*self.txRate/n
            for feeRate in feeRates
        ]

        return byteRates, self.txRate

    def generateTxSample(self, expectedNumTxs):
        k = poissonSample(expectedNumTxs)
        n = len(self.txSamples)
        try:
            return [self.txSamples[int(random()*n)] for i in range(k)]
        except IndexError:
            return [choice(self.txSamples) for i in range(k)]

    @staticmethod
    def loadObject(saveRatesFile=saveRatesFile):
        return super(TxRates, TxRates).loadObject(saveRatesFile)


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

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


# It's not enough that the tx has no deps at the time of block inclusion.
# It must have no deps at the time of mempool entry.
class TxWaitTimes(Saveable):
    def __init__(self, feeClassValues, waitTimesWindow=defaultWaitTimesWindow, saveWaitFile=saveWaitFile):
        self.blockWaitTimes = {}
        self.feeClassValues = feeClassValues
        self.waitTimes = {}
        self.waitTimesWindow = waitTimesWindow
        self.waitTimesCache = {}
        self.bestHeight = None
        self.blacklist = set()
        super(TxWaitTimes, self).__init__(saveWaitFile)

    def pushBlocks(self, blocks, init=False):
        for block in blocks:
            if not block:
                continue
            self.blockWaitTimes[block.height] = BlockTxWaitTimes(self.feeClassValues)
            blocktxidset = set(block.entries)
            self.blacklist = self.blacklist & blocktxidset
            whitelist = blocktxidset - self.blacklist
            for txid in whitelist:
                entry = block.entries[txid]
                if self._countTx(entry):
                    self.blockWaitTimes[block.height].addTx(
                        entry['feeRate'], block.time - entry['time'])
                if self._toBlacklist(entry):
                    self.blacklist.add(txid)
            logWrite("WT: Added block %d" % block.height)

            heightThresh = block.height - self.waitTimesWindow
            for height in self.blockWaitTimes.keys():
                if height <= heightThresh:
                    del self.blockWaitTimes[height]

            if not init:
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

    def getWaitTimes(self):
        numBlocks = len(self.blockWaitTimes)
        if len(self.blockWaitTimes) < minWaitBlocks:
            raise ValueError("Not enough wait blocks")
        with waitLock:
            waitTimes = self.waitTimesCache.items()
            waitTimes.sort()
            return (waitTimes, numBlocks, self.waitTimesWindow)

    @staticmethod
    def _countTx(entry):
        return (
            entry['inBlock'] and
            not entry['depends'] and
            entry['currentpriority'] < priorityThresh
        )

    @staticmethod
    def _toBlacklist(entry):
        return (
            not entry['inBlock'] and
            entry['depends']
        )

    @staticmethod
    def loadObject(saveWaitFile=saveWaitFile):
        return super(TxWaitTimes,TxWaitTimes).loadObject(saveWaitFile)


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


