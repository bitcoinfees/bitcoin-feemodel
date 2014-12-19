from feemodel.txmempool import Block
from feemodel.nonparam import BlockStat
from feemodel.util import proxy
from bitcoin.wallet import CBitcoinAddress
from collections import defaultdict
from math import log, exp, ceil

poolHistoryUsed = 432
txRateHistoryUsed = 12
feeResolution = 1000
useBootstrap = False

class MiningPool(object):
    def __init__(self, name, proportion, avgTxSize, policy=None, blockHeights=None):
        self.name = name
        self.proportion = proportion
        self.avgTxSize = avgTxSize
        self.sizeAdjusted = False
        
        if policy:
            self.maxBlockSize = policy['maxBlockSize']
            self.minFeeRate = policy['minFeeRate']
        else:
            self.maxBlockSize = None
            self.minFeeRate = None
        
        if blockHeights:
            self.estimateParams(blockHeights)

    def estimateParams(self, blockHeights):
        self.blockHeights = blockHeights
        self.maxBlockSize = None
        self.minFeeRate = None
        self.feeEstimate = None

        blockStatTotal = None
        deferredBlocks = []

        for height in blockHeights:
            block = Block.blockFromHistory(height)
            if block:
                if block.size > self.maxBlockSize:
                    self.maxBlockSize = block.size
                    deferredBlocks.append(block)
                    continue
                if self.maxBlockSize - block.size > self.avgTxSize:
                    blockStat = self._addBlock(block)
                    if blockStatTotal:
                        blockStatTotal.feeStats += blockStat.feeStats
                    else:                    
                        blockStatTotal = blockStat

        for block in deferredBlocks:
            if self.maxBlockSize - block.size > self.avgTxSize:
                blockStat = self._addBlock(block)
                if blockStatTotal:
                    blockStatTotal.feeStats += blockStat.feeStats
                else:                    
                    blockStatTotal = blockStat

        if not blockStatTotal:
            blockStatTotal = self._addBlock(deferredBlocks[0])

        blockStatTotal.feeStats.sort(key=lambda x: x.feeRate, reverse=True)
        self.feeEstimate = blockStatTotal.calcFee()
        self.minFeeRate = self.feeEstimate.mfr95 if useBootstrap else self.feeEstimate.minFeeRate


        # If a pool has fewer than X blocks, use the average max block size of all the pools

    def __repr__(self):
        return "MP{Name: %s, Prop: %.2f, Size: %d, MFR: %.0f, Adj: %s, %s}" % (
            self.name, self.proportion, self.maxBlockSize, self.minFeeRate, self.sizeAdjusted, self.feeEstimate)

    @staticmethod
    def _addBlock(block):
        try:
            minLeadTime = min([entry['leadTime'] for entry in 
                block.entries.itervalues() if entry['inBlock']])
        except ValueError:
            minLeadTime = 0
        return BlockStat(block,minLeadTime,bootstrap=useBootstrap)


class Simul(object):
    def __init__(self, currHeight=None):
        self.currHeight = currHeight if currHeight else proxy.getblockcount()
        self.pools = None
        self.numPoolsEff = None
        self.txSamples = None
        self.txRate = None
        self.avgTxSize = None
        self.estimateTxRate()
        self.estimatePools()
        self.pools.sort(key=lambda x: x.proportion, reverse=True)

    def calcIORates(self):
        self.maxMFR = int(max([pool.minFeeRate for pool in self.pools
            if pool.minFeeRate != float("inf")]) // feeResolution + 1)
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










