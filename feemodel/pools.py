from feemodel.txmempool import Block
from feemodel.util import proxy, logWrite, getCoinbaseInfo, Saveable, StoppableThread, pickle, DataSample
from feemodel.model import ModelError
from feemodel.config import savePoolsFile, poolInfoFile, config, historyFile
from feemodel.stranding import txPreprocess, calcStrandingFeeRate
from feemodel.plotting import poolsBubbleGraph, poolsRatesGraph
from bitcoin.wallet import CBitcoinAddress
from collections import defaultdict
from math import log, exp, ceil
from operator import add
from copy import deepcopy, copy
from random import random
import threading
import json
import os


hardMaxBlockSize = config['hardMaxBlockSize']
defaultPoolBlocksWindow = 2016
poolsCacheLock = threading.RLock()
defaultMinPoolBlocks = 144 # Minimum number of blocks used to estimate pools
getMFRSpacing = 5 # The percentage spacing when using getpoolmfr

class Pool(object):
    def __init__(self):
        self.proportion = -1
        self.blockHeights = set()
        self.unknown = True
        self.resetParams()

    def resetParams(self):
        self.maxBlockSize = 0
        self.minFeeRate = float("inf")
        self.feeLimitedBlocks = []
        self.sizeLimitedBlocks = []
        self.stats = {}

    def estimateParams(self, stopFlag=None, dbFile=historyFile):
        # Remember to de-duplicate blockHeights
        # and also to clear history <tick>
        txs = []
        deferredBlocks = []
        self.resetParams()

        for height in self.blockHeights:
            if stopFlag and stopFlag.is_set():
                raise ValueError("Pool estimation terminated.")
            block = Block.blockFromHistory(height, dbFile)
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
            # All the blocks are close to the max block size. We take the smallest block.
            block = min(deferredBlocks, key=lambda block: block.size)
            txs.extend(txPreprocess(block, removeHighPriority=True, removeDeps=True))

        txs.sort(key=lambda x: x[0], reverse=True)

        try:
            self.stats = calcStrandingFeeRate(txs)
        except ValueError:
            pass
        else:
            self.minFeeRate = self.stats['sfr']


        # If a pool has fewer than X blocks, use the average max block size of all the pools
        # Nah maybe not.

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
        if self.blockHeights:
            return max(self.blockHeights)
        else:
            return None

    def getNumBlocks(self):
        return len(self.feeLimitedBlocks) + len(self.sizeLimitedBlocks)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return "MP{NumBlocks: %d, Prop: %.2f, Size: %d, MFR: %.0f, %s}" % (
            len(self.feeLimitedBlocks)+len(self.sizeLimitedBlocks),
            self.proportion, self.maxBlockSize, self.minFeeRate, self.stats)


class PoolEstimator(Saveable):
    def __init__(self, poolBlocksWindow=defaultPoolBlocksWindow, minPoolBlocks=defaultMinPoolBlocks,
            savePoolsFile=savePoolsFile):
        self.pools = defaultdict(Pool)
        self.poolsCache = {}
        self.poolBlocksWindow = poolBlocksWindow
        self.minPoolBlocks = minPoolBlocks
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

    def runEstimate(self, blockHeightRange, stopFlag=None, dbFile=historyFile):
        try:
            self.identifyPoolBlocks(blockHeightRange, stopFlag=stopFlag)
            self.estimatePools(stopFlag=stopFlag, dbFile=dbFile)
        except ValueError as e:
            self.pools = deepcopy(self.poolsCache)
            logWrite(str(e))
        else:
            logWrite("Pool estimate updated %s" % self)

    def estimatePools(self, stopFlag=None, dbFile=historyFile):
        for name, pool in self.pools.items():
            pool.estimateParams(stopFlag=stopFlag, dbFile=dbFile)
            logWrite("Done estimating %s " % name)

        self.calcPoolProportions()

        with poolsCacheLock:
            self.poolsCache = deepcopy(self.pools)
            poolItems = self.poolsCache.items()
            poolItems.sort(key=lambda x: x[1].proportion, reverse=True)
            self.poolsIdx = []
            p = 0.
            for name, pool in poolItems:
                if pool.proportion > 0:
                    p += pool.proportion
                    self.poolsIdx.append((p, name, pool))
            assert abs(p-1) < 0.001

    def calcPoolProportions(self):
        totalBlocks = float(sum([pool.getNumBlocks() for pool in self.pools.values()]))
        if not totalBlocks:
            raise ValueError("No blocks found.")
        for pool in self.pools.values():
            pool.proportion = pool.getNumBlocks() / totalBlocks

        logP = [pool.proportion*log(pool.proportion) if pool.proportion else 0
            for pool in self.pools.values()]
        self.numPoolsEff = exp(-sum(logP))

    def identifyPoolBlocks(self, blockHeightRange, stopFlag=None):
        loadedHeights = reduce(add,
            [list(pool.blockHeights) for pool in self.pools.values()], [])

        for height in range(*blockHeightRange):
            if stopFlag and stopFlag.is_set():
                raise ValueError("Pool estimation terminated.")
            if height in loadedHeights:
                continue
            try:
                addr, tag = getCoinbaseInfo(blockHeight=height)
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

            # Must check if block was added to two pools based on tag and addr
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

        self.unseenInfo = {'coinbase_tags': [], 'payout_addresses': []}
        for infotype in self.unseenInfo:
            for pinfo, poolprops in self.poolInfo[infotype].items():
                if not poolprops['seen_heights']:
                    self.unseenInfo[infotype].append(pinfo)

    def selectRandomPool(self):
        self.checkNumBlocks()
        with poolsCacheLock:
            if not len(self.poolsCache):
                raise ValueError("No valid pools.")
            r = random()
            for pidx in self.poolsIdx:
                if r < pidx[0]:
                    return pidx[1], pidx[2].maxBlockSize, pidx[2].minFeeRate

            raise IndexError("This shouldn't happen")

    def getProcessingRate(self, blockRate):
        '''mfrs, processingRate, processingRateUpper = PoolEstimator.getProcessingRate(self, blockRate)'''
        self.checkNumBlocks()
        with poolsCacheLock:
            mfrs = self.getPoolMFR()
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

    def getPools(self):
        self.checkNumBlocks()
        with poolsCacheLock:
            pitems = [(name, pool.proportion, pool.maxBlockSize, pool.minFeeRate, pool.stats)
                for name, pool in self.poolsCache.items()]
            pitems.sort(key=lambda x: x[1], reverse=True)

            return pitems

    def getPoolMFR(self):
        with poolsCacheLock:
            pools = [(pool.minFeeRate, pool.proportion*pool.maxBlockSize)
                for pool in self.poolsCache.values()]
            pools.sort(key=lambda x: x[0])
            mfrs = [p[0] for p in pools]
            rates = [p[1] for p in pools]

            poolsD = DataSample(mfrs)
            feeValues = [poolsD.getPercentile((i+0.5)*getMFRSpacing/100., weights=rates)
                         for i in range(int(100 // getMFRSpacing))]

            feeValues = filter(lambda x: x < float("inf"), feeValues)

            return feeValues


    def calcCapacities(self, tr, blockRate):
        with poolsCacheLock:
            mfrs = sorted(set([pool.minFeeRate for pool in self.poolsCache.values()]))
            txByteRates, dum = tr.getByteRate(mfrs)
            binnedRates = [txByteRates[idx] - txByteRates[idx+1]
                           for idx in range(len(txByteRates)-1)] + [txByteRates[-1]]
            poolCapacities = {name: PoolCapacity(mfrs, pool, blockRate) for name, pool in self.poolsCache.items()}
            excessRates = {feeRate: 0. for feeRate in mfrs}

            for feeRate, binnedRate in reversed(zip(mfrs, binnedRates)):
                #print("FeeRate: %d, BinnedRate: %.2f" % (feeRate, binnedRate))
                excessRate = binnedRate
                while excessRate > 0:
                    nonMaxedPools = [name for name,pool in poolCapacities.items()
                                     if pool.capacities[feeRate][0] < pool.capacities[feeRate][1]]
                    if not nonMaxedPools:
                        excessRates[feeRate] = excessRate
                        break
                    totalProportion = sum([poolCapacities[name].proportion for name in nonMaxedPools])
                    for name in nonMaxedPools:
                        pool = poolCapacities[name]
                        rateAlloc = pool.proportion * excessRate / totalProportion
                        pool.capacities[feeRate][0] += rateAlloc
                        pool.capacities[feeRate][0] = min(pool.capacities[feeRate][0],
                                                          pool.capacities[feeRate][1])
                    excessRate = binnedRate - sum([pool.capacities[feeRate][0]
                                                   for pool in poolCapacities.values()])
                for pool in poolCapacities.values():
                    pool.updateCapacities()

            aggregateCap = [
                (feeRate, [sum([pool.capacities[feeRate][0] for pool in poolCapacities.values()]),
                           sum([pool.capacities[feeRate][1] for pool in poolCapacities.values()])])
                for feeRate in mfrs
            ]
            excessRates = sorted(excessRates.items(), key=lambda x: x[0])

            return aggregateCap, excessRates, poolCapacities

    def getBestHeight(self):
        with poolsCacheLock:
            try:
                bestHeight = max([pool.getBestHeight() for pool in self.poolsCache.values()])
            except ValueError:
                bestHeight = 0

            return bestHeight

    def checkNumBlocks(self):
        if self.getNumBlocks() < self.minPoolBlocks:
            raise ValueError("Too few pool blocks.")

    def getNumBlocks(self):
        with poolsCacheLock:
            return sum([pool.getNumBlocks() for pool in self.poolsCache.values()])

    @staticmethod
    def loadObject(savePoolsFile=savePoolsFile):
        return super(PoolEstimator, PoolEstimator).loadObject(savePoolsFile)

    def copyObject(self):
        '''We return a copy in which poolsCache integrity is preserved'''
        with poolsCacheLock:
            return deepcopy(self)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return "PE{NumPoolsEff: %.2f, TotalNumPools: %d, TotalUnknown: %d}" % (
            self.numPoolsEff, len(self.poolsCache), self.numUnknownPools)


class PoolEstimatorOnline(StoppableThread):
    '''Compute pool estimates every <poolEstimatePeriod> blocks
    '''
    def __init__(self, pe, poolEstimatePeriod):
        super(PoolEstimatorOnline, self).__init__()
        self.pe = pe
        self.poolEstimatePeriod = poolEstimatePeriod

    def run(self):
        logWrite("Starting pool estimator.")
        while not self.isStopped():
            self.updateEstimates()
            self.sleep(600)
        logWrite("Closed up pool estimator.")

    def updateEstimates(self):
        bestHeight = self.pe.getBestHeight()
        currHeight = proxy.getblockcount()
        if currHeight - bestHeight > self.poolEstimatePeriod:
            blockHeightRange = (currHeight-self.pe.poolBlocksWindow+1, currHeight+1)
            self.pe.runEstimate(blockHeightRange, self.getStopObject())
            try:
                self.pe.saveObject()
            except Exception as e:
                logWrite("Error saving PoolEstimator.")
                logWrite(str(e))
            self.updatePlotly()

    def updatePlotly(self, async=True):
        poolstats = self.pe.getPools()
        finalPools = []
        totalProp = 0.
        for pool in poolstats:
            totalProp += pool[1]
            if pool[3] != float("inf"):
                finalPools.append(pool)
            if totalProp >= 0.95:
                break
        t = threading.Thread(target=poolsBubbleGraph.updateAll,
                             args=(finalPools,))
        t.start()
        if not async:
            t.join()

        poolmfrs, procRate, procRateUpper = self.pe.getProcessingRate(1.)
        t = threading.Thread(target=poolsRatesGraph.updateAll,
                             args=(poolmfrs, procRate, procRateUpper))
        t.start()
        if not async:
            t.join()


class PoolCapacity(object):
    def __init__(self, feeRates, pool, blockRate):
        self.capacities = {
            feeRate: [0., 0.] for feeRate in feeRates
        }
        self.proportion = pool.proportion
        self.maxCap = pool.maxBlockSize*blockRate*pool.proportion
        self.minFeeRate = pool.minFeeRate
        self.updateCapacities()

    def updateCapacities(self):
        feeRates = sorted(self.capacities.keys(), reverse=True)
        residualCap = self.maxCap
        for f in feeRates:
            self.capacities[f][1] = residualCap if f >= self.minFeeRate else 0.
            residualCap = max(self.capacities[f][1] - self.capacities[f][0], 0)



