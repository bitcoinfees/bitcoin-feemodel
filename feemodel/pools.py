from feemodel.txmempool import Block
from feemodel.util import proxy, logWrite, getCoinbaseInfo, Saveable, StoppableThread
from feemodel.model import ModelError
from feemodel.config import savePoolsFile, poolInfoFile, config
from feemodel.stranding import txPreprocess, calcStrandingFeeRate
from bitcoin.wallet import CBitcoinAddress
from collections import defaultdict
from math import log, exp, ceil
from operator import add
from copy import deepcopy, copy
from random import random
import threading
import json
import os

try:
    import cPickle as pickle
except ImportError:
    import pickle

hardMaxBlockSize = config['hardMaxBlockSize']
defaultPoolBlocksWindow = 2016
poolsCacheLock = threading.RLock()

class Pool(object):
    def __init__(self):
        self.proportion = -1
        self.maxBlockSize = 0
        self.minFeeRate = float("inf")
        self.blockHeights = set()
        self.feeLimitedBlocks = []
        self.sizeLimitedBlocks = []
        self.stats = {}
        self.unknown = True

    def estimateParams(self, stopFlag=None):
        # Remember to de-duplicate blockHeights
        # and also to clear history <tick>
        txs = []
        deferredBlocks = []

        for height in self.blockHeights:
            if stopFlag and stopFlag.is_set():
                return
            block = Block.blockFromHistory(height)
            if block:
                blockTxs = [tx for tx in block.entries.itervalues() if tx['inBlock']]
                if blockTxs:
                    block.avgTxSize = sum([tx['size'] for tx in blockTxs]) / float(len(blockTxs))
                else:
                    block.avgTxSize = 0
                if block.size > self.maxBlockSize:
                    self.maxBlockSize = block.size
                    deferredBlocks.append(block)
                    continue
                self.addBlock(block, txs)

        for block in deferredBlocks:
            self.addBlock(block, txs)

        if not txs and deferredBlocks:
            txs.extend(txPreprocess(deferredBlocks[0], removeHighPriority=True, removeDeps=True))

        txs.sort(key=lambda x: x[0], reverse=True)

        try:
            self.stats = calcStrandingFeeRate(txs)
            self.minFeeRate = self.stats['sfr']
        except ValueError:
            pass

        logWrite("Done estimating %s " % repr(self))

        # If a pool has fewer than X blocks, use the average max block size of all the pools

    def addBlock(self, block, txs):
        if self.maxBlockSize - block.size > block.avgTxSize:
            self.feeLimitedBlocks.append([block.height, block.size])
            txsNew = txPreprocess(block, removeHighPriority=True, removeDeps=True)
            txs.extend(txsNew)
        else:
            self.sizeLimitedBlocks.append([block.height, block.size])

    def clearHeights(self, heightThresh):
        self.blockHeights = set(filter(lambda x: x > heightThresh, self.blockHeights))

    def getBestHeight(self):
        maxFeeLimited = max(self.feeLimitedBlocks) if self.feeLimitedBlocks else None
        maxSizeLimited = max(self.sizeLimitedBlocks) if self.sizeLimitedBlocks else None
        return max(maxFeeLimited, maxSizeLimited)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return "MP{Prop: %.2f, Size: %d, MFR: %.0f, %s}" % (
            self.proportion, self.maxBlockSize, self.minFeeRate, self.stats)


class PoolEstimator(Saveable):
    def __init__(self, poolBlocksWindow=defaultPoolBlocksWindow, savePoolsFile=savePoolsFile):
        self.pools = defaultdict(Pool)
        self.poolsCache = {}
        self.poolBlocksWindow = poolBlocksWindow
        self.numUnknownPools = 0
        self.numPoolsEff = 0.

        try:
            with open(poolInfoFile, 'r') as f:
                self.poolInfo = json.load(f)
        except IOError as e:
            logWrite("Error opening poolInfoFile: pools.json.")
            raise e
        else:
            for idtype in self.poolInfo:
                for poolprops in self.poolInfo[idtype].values():
                    poolprops['seen_heights'] = set()
        super(PoolEstimator, self).__init__(savePoolsFile)

    def runEstimate(self, blockHeightRange, stopFlag=None):
        self.identifyPoolBlocks(blockHeightRange, stopFlag)
        self.estimatePools(stopFlag)
        if stopFlag and stopFlag.is_set():
            return
        try:
            self.saveObject()
        except IOError:
            logWrite("Error saving PoolEstimator.")

        logWrite("Pool estimate updated %s" % self)

    def estimatePools(self, stopFlag=None):
        for pool in self.pools.values():
            if stopFlag and stopFlag.is_set():
                self.pools = deepcopy(self.poolsCache) # Restore to previous state
                return
            pool.estimateParams(stopFlag)
        with poolsCacheLock:
            self.poolsCache = deepcopy(self.pools)
            self.poolsIdx = []
            p = 0.
            for name, pool in self.poolsCache.iteritems():
                p += pool.proportion
                self.poolsIdx.append((p, name, pool))

    def identifyPoolBlocks(self, blockHeightRange, stopFlag=None):
        loadedHeights = reduce(add,
            [list(pool.blockHeights) for pool in self.pools.values()], [])

        for height in range(*blockHeightRange):
            if stopFlag and stopFlag.is_set():
                return
            if height in loadedHeights:
                continue
            try:
                addr, tag = getCoinbaseInfo(height)
            except IndexError:
                logWrite("Exceeds best block height.")
                continue
            # this is not quite correct.
            foundAddr = foundTag = False

            for pooladdr, poolprops in self.poolInfo['payout_addresses'].items():
                if pooladdr == addr:
                    assert not foundAddr
                    # if foundAddr:
                        # assert poolprops['name'] in self.pools
                    self.pools[poolprops['name']].blockHeights.add(height)
                    self.pools[poolprops['name']].unknown = False
                    poolprops['seen_heights'].add(height)
                    foundAddr = True
            for pooltag, poolprops in self.poolInfo['coinbase_tags'].items():
                if pooltag in tag:
                    assert not foundTag
                    # if foundTag:
                    #     assert poolprops['name'] in self.pools
                    self.pools[poolprops['name']].blockHeights.add(height)
                    self.pools[poolprops['name']].unknown = False
                    poolprops['seen_heights'].add(height)
                    foundTag = True

            if not foundAddr and not foundTag:
                self.pools[addr].blockHeights.add(height)
                self.pools[addr].unknown = True
            logWrite("idPoolBlocks: added height %d" % height)

        heightThresh = height - self.poolBlocksWindow
        for name, pool in self.pools.items():
            pool.clearHeights(heightThresh)
            if not len(pool.blockHeights):
                del self.pools[name]

        self.numUnknownPools = sum([pool.unknown for pool in self.pools.values()])

        totalBlocks = float(sum([len(pool.blockHeights) for pool in self.pools.values()]))
        for pool in self.pools.values():
            pool.proportion = len(pool.blockHeights) / totalBlocks

        logP = [pool.proportion*log(pool.proportion) if pool.proportion else 0
            for pool in self.pools.values()]
        self.numPoolsEff = exp(-sum(logP))

        self.unseenInfo = {'coinbase_tags': [], 'payout_addresses': []}
        for infotype in self.unseenInfo:
            for pinfo, poolprops in self.poolInfo[infotype].items():
                if not poolprops['seen_heights']:
                    self.unseenInfo[infotype].append(pinfo)

    def selectRandomPool(self):
        with poolsCacheLock:
            if not len(self.poolsCache):
                raise ValueError("No valid pools.")
            r = random()
            for pidx in self.poolsIdx:
                if r < pidx[0]:
                    return pidx[2].maxBlockSize, pidx[2].minFeeRate

            raise IndexError("This shouldn't happen")

    def getProcessingRate(self, blockRate):
        with poolsCacheLock:
            mfrs = self.getPoolMFR()
            mfrs = filter(lambda x: x < float("inf"), mfrs)
            mfrs.sort()
            processingRate = [
                sum([pool.proportion*pool.maxBlockSize*blockRate
                    for pool in self.poolsCache.values()
                    if pool.minFeeRate <= mfr])
                for mfr in mfrs
            ]
            processingRateUpper = [
                sum([pool.proportion*hardMaxBlockSize*blockRate
                    for pool in self.poolsCache.values()
                    if pool.minFeeRate <= mfr])
                for mfr in mfrs
            ]
            return mfrs, processingRate, processingRateUpper

    def getPoolMFR(self):
        with poolsCacheLock:
            return [pool.minFeeRate for pool in self.poolsCache.values()]

    def getBestHeight(self):
        with poolsCacheLock:
            try:
                bestHeight = max([pool.getBestHeight() for pool in self.poolsCache.values()])
            except ValueError:
                bestHeight = None

            return bestHeight

    @staticmethod
    def loadObject(savePoolsFile=savePoolsFile):
        return super(PoolEstimator, PoolEstimator).loadObject(savePoolsFile)

    def saveObject(self):
        with poolsCacheLock:
            super(PoolEstimator, self).saveObject()

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return "PE{NumPoolsEff: %.2f, TotalNumPools: %d, TotalUnknown: %d}" % (
            self.numPoolsEff, len(self.poolsCache), self.numUnknownPools)


class PoolEstimatorOnline(StoppableThread, PoolEstimator):
    '''Compute pool estimates every <poolEstimatePeriod> blocks
    '''
    def __init__(self, poolBlocksWindow, poolEstimatePeriod):
        self.poolEstimatePeriod = poolEstimatePeriod
        StoppableThread.__init__(self)
        PoolEstimator.__init__(poolBlocksWindow)
        try:
            peSave = PoolEstimator.loadObject()
        except IOError:
            logWrite("Unable to load pool estimator; loading from scratch.")
        else:
            for key,val in peSave.__dict__.items():
                setattr(self, key, val)

    def run(self):
        logWrite("Starting pool estimator.")
        while not self.isStopped():
            self.updateEstimates()
            self.sleep(600)
        logWrite("Closed up pool estimator.")

    def updateEstimates(self):
        bestHeight = self.getBestHeight()
        currHeight = proxy.getblockcount()
        if not bestHeight or currHeight - bestHeight > poolEstimatePeriod:
            blockHeightRange = (currHeight-self.poolBlocksWindow+1, currHeight+1)
            self.runEstimate(blockHeightRange, self.getStopObject())


