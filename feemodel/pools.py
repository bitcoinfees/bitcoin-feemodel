from feemodel.txmempool import Block
from feemodel.util import proxy, logWrite, getCoinbaseInfo, Saveable
from feemodel.model import ModelError
from feemodel.config import savePoolsFile, poolInfoFile, config
from feemodel.stranding import txPreprocess, calcStrandingFeeRate
from bitcoin.wallet import CBitcoinAddress
from collections import defaultdict
from math import log, exp, ceil
from operator import add
from copy import deepcopy
from random import random
import threading
import json
import os

try:
    import cPickle as pickle
except ImportError:
    import pickle

poolHistoryUsed = 36
txRateHistoryUsed = 12
feeResolution = 1000

hardMaxBlockSize = config['hardMaxBlockSize']
poolBlocksWindow = 2016
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

    def estimateParams(self):
        # Remember to de-duplicate blockHeights
        # and also to clear history <tick>
        txs = []
        deferredBlocks = []

        for height in self.blockHeights:
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

    def clearHeights(self, bestHeight):
        heightThresh = bestHeight - poolBlocksWindow
        self.blockHeights = set(filter(lambda x: x > heightThresh, self.blockHeights))


    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        return "MP{Prop: %.2f, Size: %d, MFR: %.0f, %s}" % (
            self.proportion, self.maxBlockSize, self.minFeeRate, self.stats)


class PoolEstimator(Saveable):
    def __init__(self, savePoolsFile=savePoolsFile):
        self.pools = defaultdict(Pool)
        self.poolsCache = {}

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

    def estimatePools(self):
        for pool in self.pools.values():
            pool.estimateParams()
        with poolsCacheLock:
            self.poolsCache = deepcopy(self.pools)
            self.poolsIdx = []
            p = 0.
            for name, pool in self.poolsCache.iteritems():
                p += pool.proportion
                self.poolsIdx.append((p, name, pool))

    def identifyPoolBlocks(self, blockHeightRange):
        loadedHeights = reduce(add,
            [list(pool.blockHeights) for pool in self.pools.values()], [])

        for height in range(*blockHeightRange):
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

        for name, pool in self.pools.items():
            pool.clearHeights(height)
            if not len(pool.blockHeights):
                del self.pools[name]

        self.numUnknownPools = sum([pool.unknown for pool in self.pools.values()])

        totalBlocks = float(sum([len(pool.blockHeights) for pool in self.pools.values()]))
        for pool in self.pools.values():
            pool.proportion = len(pool.blockHeights) / totalBlocks

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
                    return pidx[1], deepcopy(pidx[2])

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

    @staticmethod
    def loadObject(savePoolsFile=savePoolsFile):
        return super(PoolEstimator, PoolEstimator).loadObject(savePoolsFile)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    # def loadPools(self, dbFile=savePoolsFile):
    #     db = None
    #     try:
    #         db = sqlite3.connect(dbFile)
    #         poolList = db.execute('SELECT * FROM pools').fetchall()
    #         for name,poolJSON in poolList:
    #             self.pools[name] = Pool.fromJSON(poolJSON)
    #         self.calcNumUnknownPools()
    #         self.calcUnseenInfo()
    #     finally:
    #         if db:
    #             db.close()

    # def savePools(self, dbFile=savePoolsFile):
    #     if not len(self.pools):
    #         raise ValueError("No pools to save.")
    #     dbExists = False if not os.path.exists(dbFile) else True
    #     db = None
    #     try:
    #         db = sqlite3.connect(dbFile)
    #         if not dbExists:
    #             db.execute('CREATE TABLE pools (name TEXT, data TEXT)')
    #         with db:
    #             db.execute('DELETE FROM pools')
    #             db.executemany('INSERT INTO pools VALUES (?, ?)', 
    #                 [(name,pool.toJSON()) for name,pool in self.pools.items()])
    #     finally:
    #         if db:
    #             db.close()





# class Simul(object):
#     def __init__(self, currHeight=None, adaptive=0):
#         self.currHeight = currHeight if currHeight else proxy.getblockcount()
#         self.pools = None
#         self.numPoolsEff = None
#         self.txSamples = None
#         self.txRate = None
#         self.avgTxSize = None
#         self.serviceRates = None
#         self.arrivalRates = None
#         self.adaptive = adaptive
#         if not adaptive:
#             self.estimateTxRate()
#             self.estimatePools()
#             self.calcIORates()
#             self.pools.sort(key=lambda x: x.proportion, reverse=True)
#         self.qMetrics = [FeeClass(i*feeResolution, adaptive=adaptive)
#             for i in range(len(self.serviceRates))]

#     def pushBlock(self, blockInterval, minFeeRate, currHeight=None, blockHeight=None):
#         for feeClass in self.qMetrics:
#             feeClass.pushBlock(blockInterval,minFeeRate,currHeight,blockHeight)

#     def calcIORates(self):
#         # self.maxMFR = int(max([pool.minFeeRate for pool in self.pools
#         #     if pool.minFeeRate != float("inf")]) // feeResolution + 1)
#         poolMFR = [pool.minFeeRate for pool in self.pools if pool.minFeeRate != float("inf")]
#         poolMFR.sort()
#         mfrIdx = int(mfrPoolPercentile*len(poolMFR)//100)
#         self.maxMFR = poolMFR[mfrIdx] // feeResolution + 1

#         self.serviceRates = [sum([pool.proportion*pool.maxBlockSize/10/60 for pool in self.pools
#             if pool.minFeeRate <= n*feeResolution]) for n in range(self.maxMFR+1)] # per hour, in bytes
#         self.arrivalRates = [sum([tx[0] for tx in self.txSamples
#             if tx[1] >= n*feeResolution])*self.txRate/len(self.txSamples)
#             for n in range(self.maxMFR+1)]

#     def adjustMaxBlockSizes(self):
#         pass
#         # for pool in self.pools:
#         #     if pool.minFeeRate != float("inf"):
#         #         arrivalRate = self.arrivalRates[int(pool.minFeeRate // feeResolution)]
#         #     else:
#         #         arrivalRate = 0

#         #     p90size = arrivalRate*1381
#         #     if pool.maxBlockSize < p90size:
#         #         pool.sizeAdjusted = True

#         # avgBlockSize = sum([pool.maxBlockSize*pool.proportion
#         #     for pool in self.pools if not pool.sizeAdjusted]) / sum([pool.proportion
#         #     for pool in self.pools if not pool.sizeAdjusted])
#         # for pool in self.pools:
#         #     if pool.sizeAdjusted:
#         #         pool.maxBlockSize = avgBlockSize


#     def estimatePools(self):
#         poolBlocks = defaultdict(list)
#         heightRange = range(self.currHeight - poolHistoryUsed, self.currHeight+1)

#         for height in heightRange:
#             block = proxy.getblock(proxy.getblockhash(height))
#             coinbaseTx = block.vtx[0]
#             assert coinbaseTx.is_coinbase()
#             coinbaseAddr = str(CBitcoinAddress.from_scriptPubKey(coinbaseTx.vout[0].scriptPubKey))
#             poolBlocks[coinbaseAddr] += [height]

#         # Compute effective number of pools
#         poolCounts = [len(heights) for heights in poolBlocks.itervalues()]
#         totalBlocks = sum(poolCounts)
#         poolP = [float(count) / totalBlocks for count in poolCounts]

#         self.numPoolsEff = exp(-sum([p*log(p) for p in poolP]))

#         self.pools = []
#         for pool,heights in poolBlocks.items():
#             print("Adding pool " + pool)
#             self.pools.append(MiningPool(
#                 pool,len(heights)/float(totalBlocks),self.avgTxSize,blockHeights=heights))

#     def estimateTxRate(self):
#         heightRange = range(self.currHeight - txRateHistoryUsed, self.currHeight+1)
#         prevBlock = None
#         self.txSamples = []
#         totalTime = 0

#         for height in heightRange:
#             print(height)
#             block = Block.blockFromHistory(height)
#             if block:
#                 if not prevBlock:
#                     prevBlock = block
#                     continue
#                 if height == prevBlock.height + 1:
#                     newtxs = set(block.entries) - set(prevBlock.entries)
#                     self.txSamples += [(block.entries[txid]['size'],block.entries[txid]['feeRate'])
#                         for txid in newtxs]
#                     totalTime += block.time - prevBlock.time
#                 prevBlock = block

#         self.txRate = len(self.txSamples) / float(totalTime)
#         self.avgTxSize = sum([s[0] for s in self.txSamples])/float(len(self.txSamples))






