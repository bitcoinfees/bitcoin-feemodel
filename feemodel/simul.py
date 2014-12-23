from feemodel.txmempool import Block
from feemodel.nonparam import BlockStat
from feemodel.util import proxy, logWrite
from feemodel.model import ModelError
from feemodel.config import savePoolBlocksFile, savePoolsFile
from bitcoin.wallet import CBitcoinAddress
from collections import defaultdict
from math import log, exp, ceil
from operator import add
import json
import sqlite3
import os

try:
    import cPickle as pickle
except ImportError:
    import pickle

poolHistoryUsed = 36
txRateHistoryUsed = 12
feeResolution = 1000
useBootstrap = False
mfrPoolPercentile = 95


class MiningPool(object):
    def __init__(self, name=None, proportion=None, initData=None):
        if initData:
            for key in initData:
                setattr(self, key, initData[key])
        else:
            self.name = name
            self.proportion = proportion
            self.maxBlockSize = None
            self.minFeeRate = None
            self.abovekn = None
            self.belowkn = None
            self.blockHeights = []
            self.feeLimitedBlocks = []
            self.sizeLimitedBlocks = []

    def estimateParams(self, blockHeights):
        self.blockHeights = blockHeights
        blockStatTotal = None
        deferredBlocks = []

        for height in blockHeights:
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
                blockStatTotal = self.addBlock(block, blockStatTotal)

        for block in deferredBlocks:
            blockStatTotal = self.addBlock(block, blockStatTotal)

        if not blockStatTotal:
            blockStatTotal = self._getBlockStat(deferredBlocks[0])

        blockStatTotal.feeStats.sort(key=lambda x: x.feeRate, reverse=True)
        feeEstimate = blockStatTotal.calcFee()
        self.minFeeRate = feeEstimate.mfr95 if useBootstrap else feeEstimate.minFeeRate
        self.abovekn = list(feeEstimate.abovekn)
        self.belowkn = list(feeEstimate.belowkn)
        logWrite("Done estimating %s " % repr(self))

        # If a pool has fewer than X blocks, use the average max block size of all the pools

    def addBlock(self, block, blockStatTotal):
        if self.maxBlockSize - block.size > block.avgTxSize:
            self.feeLimitedBlocks.append([block.height, block.size])
            blockStat = self._getBlockStat(block)
            if blockStatTotal:
                blockStatTotal.feeStats += blockStat.feeStats
            else:
                blockStatTotal = blockStat
        else:
            self.sizeLimitedBlocks.append([block.height, block.size])

        return blockStatTotal

    @staticmethod
    def _getBlockStat(block):
        try:
            minLeadTime = min([entry['leadTime'] for entry in 
                block.entries.itervalues() if entry['inBlock']])
        except ValueError:
            minLeadTime = 0
        return BlockStat(block,minLeadTime,bootstrap=useBootstrap)


    @classmethod
    def fromJSON(cls, s):
        attribs = json.loads(s)
        return cls(initData=attribs)

    def toJSON(self):
        return json.dumps(self.__dict__)

    def __repr__(self):
        return "MP{Name: %s, Prop: %.2f, Size: %d, MFR: %.0f, abovekn: %s, belowkn: %s}" % (
            self.name, self.proportion, self.maxBlockSize, self.minFeeRate, self.abovekn, self.belowkn)


class PoolEstimator(object):
    def __init__(self):
        self.pools = {}
        self.numPoolsEff = None
        self.poolBlocks = defaultdict(list)

    def getPoolBlocks(self, blockHeightRange):
        # blockHeightRange is (start, end), inclusive of start but not end - like range()
        try:
            self.loadPoolBlocks()
        except IOError:
            logWrite('PoolEst: Error loading poolBlocks')
            loadedHeights = []
        else:
            for pool,heights in self.poolBlocks.items():
                inRangeHeights = filter(lambda x: x >= blockHeightRange[0], heights)
                if inRangeHeights:
                    self.poolBlocks[pool] = inRangeHeights
                else:
                    del self.poolBlocks[pool]

            loadedHeights = reduce(add, self.poolBlocks.itervalues(), [])
            logWrite("PoolEst: Loaded %d heights" % len(loadedHeights))

        for height in range(*blockHeightRange):
            if height not in loadedHeights:
                try:
                    block = proxy.getblock(proxy.getblockhash(height))
                except IndexError:
                    logWrite("PoolEst: Invalid block height!")
                    continue
                coinbaseTx = block.vtx[0]
                assert coinbaseTx.is_coinbase()
                coinbaseAddr = str(CBitcoinAddress.from_scriptPubKey(coinbaseTx.vout[0].scriptPubKey))
                self.poolBlocks[coinbaseAddr] += [height]
                logWrite("PoolEst: Loaded height %d into poolBlocks" % (height,))

        self.calcNumPoolsEff()
        logWrite("PoolEst: Finished loading poolBlocks, with %.1f numPoolsEff" % self.numPoolsEff)
        self.savePoolBlocks()

    def estimatePools(self):
        if not self.poolBlocks:
            raise ValueError("PoolEst: empty poolBlocks.")
        for pool, heights in self.poolBlocks.items():
            proportion = len(heights) / self.totalBlocks
            self.pools[pool] = MiningPool(pool, proportion)
            self.pools[pool].estimateParams(heights)

    def loadPoolBlocks(self, dbFile=savePoolBlocksFile):
        with open(dbFile,'rb') as f:
            self.poolBlocks = pickle.load(f)

    def savePoolBlocks(self, dbFile=savePoolBlocksFile):
        if not len(self.poolBlocks):
            raise ValueError("No poolBlocks to save.")
        with open(dbFile,'wb') as f:
            pickle.dump(self.poolBlocks, f)

    def loadPools(self, dbFile=savePoolsFile):
        db = None
        try:
            db = sqlite3.connect(dbFile)
            poolList = db.execute('SELECT * FROM pools').fetchall()
            for name,poolJSON in poolList:
                self.pools[name] = MiningPool.fromJSON(poolJSON)
        finally:
            if db:
                db.close()

    def savePools(self, dbFile=savePoolsFile):
        if not len(self.pools):
            raise ValueError("No pools to save.")
        dbExists = False if not os.path.exists(dbFile) else True
        db = None
        try:
            db = sqlite3.connect(dbFile)
            if not dbExists:
                db.execute('CREATE TABLE pools (name TEXT, data TEXT)')
            with db:
                db.execute('DELETE FROM pools')
                db.executemany('INSERT INTO pools VALUES (?, ?)', 
                    [(name,pool.toJSON()) for name,pool in self.pools.items()])
        finally:
            if db:
                db.close()

    def calcNumPoolsEff(self):
        poolCounts = [len(heights) for heights in self.poolBlocks.itervalues()]
        self.totalBlocks = float(sum(poolCounts))
        poolP = [count / self.totalBlocks for count in poolCounts]

        self.numPoolsEff = exp(-sum([p*log(p) for p in poolP]))

class Simul(object):
    def __init__(self, currHeight=None, adaptive=0):
        self.currHeight = currHeight if currHeight else proxy.getblockcount()
        self.pools = None
        self.numPoolsEff = None
        self.txSamples = None
        self.txRate = None
        self.avgTxSize = None
        self.serviceRates = None
        self.arrivalRates = None
        self.adaptive = adaptive
        if not adaptive:
            self.estimateTxRate()
            self.estimatePools()
            self.calcIORates()
            self.pools.sort(key=lambda x: x.proportion, reverse=True)
        self.qMetrics = [FeeClass(i*feeResolution, adaptive=adaptive)
            for i in range(len(self.serviceRates))]

    def pushBlock(self, blockInterval, minFeeRate, currHeight=None, blockHeight=None):
        for feeClass in self.qMetrics:
            feeClass.pushBlock(blockInterval,minFeeRate,currHeight,blockHeight)

    def calcIORates(self):
        # self.maxMFR = int(max([pool.minFeeRate for pool in self.pools
        #     if pool.minFeeRate != float("inf")]) // feeResolution + 1)
        poolMFR = [pool.minFeeRate for pool in self.pools if pool.minFeeRate != float("inf")]
        poolMFR.sort()
        mfrIdx = int(mfrPoolPercentile*len(poolMFR)//100)
        self.maxMFR = poolMFR[mfrIdx] // feeResolution + 1

        self.serviceRates = [sum([pool.proportion*pool.maxBlockSize/10/60 for pool in self.pools
            if pool.minFeeRate <= n*feeResolution]) for n in range(self.maxMFR+1)] # per hour, in bytes
        self.arrivalRates = [sum([tx[0] for tx in self.txSamples
            if tx[1] >= n*feeResolution])*self.txRate/len(self.txSamples)
            for n in range(self.maxMFR+1)]

    def adjustMaxBlockSizes(self):
        pass
        # for pool in self.pools:
        #     if pool.minFeeRate != float("inf"):
        #         arrivalRate = self.arrivalRates[int(pool.minFeeRate // feeResolution)]
        #     else:
        #         arrivalRate = 0

        #     p90size = arrivalRate*1381
        #     if pool.maxBlockSize < p90size:
        #         pool.sizeAdjusted = True

        # avgBlockSize = sum([pool.maxBlockSize*pool.proportion
        #     for pool in self.pools if not pool.sizeAdjusted]) / sum([pool.proportion
        #     for pool in self.pools if not pool.sizeAdjusted])
        # for pool in self.pools:
        #     if pool.sizeAdjusted:
        #         pool.maxBlockSize = avgBlockSize


    def estimatePools(self):
        poolBlocks = defaultdict(list)
        heightRange = range(self.currHeight - poolHistoryUsed, self.currHeight+1)

        for height in heightRange:
            block = proxy.getblock(proxy.getblockhash(height))
            coinbaseTx = block.vtx[0]
            assert coinbaseTx.is_coinbase()
            coinbaseAddr = str(CBitcoinAddress.from_scriptPubKey(coinbaseTx.vout[0].scriptPubKey))
            poolBlocks[coinbaseAddr] += [height]

        # Compute effective number of pools
        poolCounts = [len(heights) for heights in poolBlocks.itervalues()]
        totalBlocks = sum(poolCounts)
        poolP = [float(count) / totalBlocks for count in poolCounts]

        self.numPoolsEff = exp(-sum([p*log(p) for p in poolP]))

        self.pools = []
        for pool,heights in poolBlocks.items():
            print("Adding pool " + pool)
            self.pools.append(MiningPool(
                pool,len(heights)/float(totalBlocks),self.avgTxSize,blockHeights=heights))

    def estimateTxRate(self):
        heightRange = range(self.currHeight - txRateHistoryUsed, self.currHeight+1)
        prevBlock = None
        self.txSamples = []
        totalTime = 0

        for height in heightRange:
            print(height)
            block = Block.blockFromHistory(height)
            if block:
                if not prevBlock:
                    prevBlock = block
                    continue
                if height == prevBlock.height + 1:
                    newtxs = set(block.entries) - set(prevBlock.entries)
                    self.txSamples += [(block.entries[txid]['size'],block.entries[txid]['feeRate'])
                        for txid in newtxs]
                    totalTime += block.time - prevBlock.time
                prevBlock = block

        self.txRate = len(self.txSamples) / float(totalTime)
        self.avgTxSize = sum([s[0] for s in self.txSamples])/float(len(self.txSamples))






