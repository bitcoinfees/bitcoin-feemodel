from feemodel.config import saveQueueFile, historyFile, config
from feemodel.txmempool import Block
from feemodel.nonparam import BlockStat
from feemodel.util import proxy, logWrite, pprint
from feemodel.model import ModelError

try:
    import cPickle as pickle
except ImportError:
    import pickle

feeResolution = config['queue']['feeResolution']
adaptiveWindow = config['queue']['adaptiveWindow']

class QEstimator(object):
    def __init__(self, maxMFR):
        self.qMetrics = [FeeClass(i*feeResolution)
            for i in range(maxMFR // feeResolution + 1)]
        self.maxMFR = maxMFR

    def nextBlock(self, blockHeight, blockInterval, minFeeRate):
        for feeClass in self.qMetrics:
            feeClass.nextBlock(blockHeight, blockInterval, minFeeRate)

    def getStats(self):
        return [repr(self)] + [(fc.feeRate, fc.avgWait, fc.strandedProportion, fc.avgStrandedBlocks)
            for fc in self.qMetrics]

    def __eq__(self, other):
        return all([
            self.qMetrics[idx] == other.qMetrics[idx]
            for idx in range(len(self.qMetrics))
        ])

class QEOnline(QEstimator):
    def __init__(self, maxMFR, adaptive=adaptiveWindow, loadFile=saveQueueFile):
        super(QEOnline, self).__init__(maxMFR)
        self.adaptive = adaptive
        self.blockData = {}
        self.bestHeight = None
        self.prevBlock = None

        if loadFile:
            try:
                self.loadBlockData(loadFile)
            except IOError:
                logWrite("Couldn't load saved blocks.")
            else:
                logWrite("Loading blocks; found best height at " + 
                    str(self.bestHeight if self.bestHeight else -1))

    def pushBlocks(self, blocks, isInit=False):
        for block in blocks:
            if block:
                if self.prevBlock and block.height == self.prevBlock.height + 1:
                    blockInterval = block.time - self.prevBlock.time
                    blockInterval = max(blockInterval, 1)
                    try:
                        minLeadTime = min([entry['leadTime'] for entry in 
                            block.entries.itervalues() if entry['inBlock']])
                    except ValueError:
                        minLeadTime = 0
                    blockStat = BlockStat(block,minLeadTime,bootstrap=False,allowZeroFee=True)
                    minFeeRate = blockStat.calcFee().minFeeRate
                    self.blockData[block.height] = (blockInterval, minFeeRate)
                    self.bestHeight = block.height
                    logWrite("Added block %d with interval %d and mfr %.0f" %
                        (block.height, blockInterval, minFeeRate))
                self.prevBlock = block

        if not isInit:
            self.adaptiveCalc()
            try:
                self.saveBlockData()
            except IOError:
                logWrite("Error saving blocks.")

    def adaptiveCalc(self):
        if not self.bestHeight:
            raise ValueError("Empty blockData.")

        heightThresh = self.bestHeight - self.adaptive
        for height in self.blockData.keys():
            if height < heightThresh:
                del self.blockData[height]

        self.qMetrics = [FeeClass(i*feeResolution)
            for i in range(self.maxMFR // feeResolution + 1)]
        blockDataItems = self.blockData.items()
        blockDataItems.sort(key=lambda x: x[0])
        for height, block in blockDataItems:
            for feeClass in self.qMetrics:
                feeClass.nextBlock(height, block[0], block[1])

    def __repr__(self):
        return "QEO{maxMFR: %d, adaptive: %d, bestHeight: %d, numBlocks: %d}" % (
            self.maxMFR, self.adaptive, self.bestHeight if self.bestHeight else -1, len(self.blockData))

    def saveBlockData(self, dbFile=saveQueueFile):
        if not self.blockData:
            raise ValueError("There's nothing to save.")

        with open(dbFile, 'wb') as f:
            pickle.dump(self.blockData, f)

    def loadBlockData(self, dbFile=saveQueueFile):
        with open(dbFile, 'rb') as f:
            self.blockData = pickle.load(f)
        try:
            self.bestHeight = max(self.blockData)
        except ValueError:
            self.bestHeight = None

    # def readFromHistory(self, blockHeightRange, dbFile=historyFile):
    #     # blockHeightRange is (start,end) inclusive at both sides
    #     # but start won't be counted because it serves as the reference for the first time diff
    #     try:
    #         bestHeight = max(self.blocks.keys())
    #     except ValueError:
    #         bestHeight = None
        
    #     prevBlock = None
    #     idx = 0
    #     heights = range(max(bestHeight,blockHeightRange[0]), blockHeightRange[1]+1)

    #     try:
    #         while not prevBlock:
    #             prevBlock = Block.blockFromHistory(heights[idx],dbFile=dbFile)
    #             idx += 1
    #     except IndexError:
    #         raise ValueError("No valid blocks.")

    #     for height in heights[idx:]:
    #         if not (height % 10):
    #             print(height)
    #         block = Block.blockFromHistory(height)
    #         if block:
    #             if height == prevBlock.height + 1:
    #                 blockInterval = block.time - prevBlock.time
    #                 blockInterval = max(blockInterval, 1)
    #                 try:
    #                     minLeadTime = min([entry['leadTime'] for entry in 
    #                         block.entries.itervalues() if entry['inBlock']])
    #                 except ValueError:
    #                     minLeadTime = 0
    #                 blockStat = BlockStat(block,minLeadTime,bootstrap=False,allowZeroFee=True)
    #                 minFeeRate = blockStat.calcFee().minFeeRate
    #                 self.blocks[height] = (blockInterval, minFeeRate)
    #             prevBlock = block


class FeeClass(object):
    def __init__(self, feeRate):
        self.feeRate = feeRate
        self.totalTime = 0.
        self.totalBlocks = 0
        self.totalStrandedPeriods = 0
        self.avgWait = 0.
        self.strandedProportion = 0.
        self.avgStrandedBlocks = 0.
        self.prevHeight = None
        self.strandedBlocks = []

    def nextBlock(self, height, blockInterval, minFeeRate):
        if not self.prevHeight or height > self.prevHeight + 1:
            self.strandedBlocks = []

        self.prevHeight = height

        stranded = self.feeRate < minFeeRate
        self.updateStrandedProportion(stranded)

        if not stranded:
            cumWait = self.updateAvgWait(blockInterval, 0)
            numStranded = len(self.strandedBlocks)
            if numStranded:
                for strandBlockInterval in reversed(self.strandedBlocks):
                    cumWait = self.updateAvgWait(strandBlockInterval, cumWait)
                self.avgStrandedBlocks = (self.avgStrandedBlocks*self.totalStrandedPeriods
                    + numStranded) / (self.totalStrandedPeriods+1)
                self.totalStrandedPeriods += 1
                self.strandedBlocks = []
        else:
            self.strandedBlocks.append(blockInterval)

    def updateAvgWait(self, thisInterval, cumWait):
        self.avgWait = (self.avgWait*self.totalTime +
            thisInterval*(thisInterval*0.5 + cumWait)) / (
            self.totalTime + thisInterval)
        self.totalTime += thisInterval
        return cumWait + thisInterval

    def updateStrandedProportion(self, stranded):        
        self.strandedProportion = (self.strandedProportion*self.totalBlocks
            + int(stranded)) / float(self.totalBlocks+1)
        self.totalBlocks += 1

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

# class QEonline(QEstimator):
#     def __init__(self, maxMFR, adaptive=adaptiveWindow, currHeight=None):
#         super(QEonline, self).__init__(maxMFR, adaptive)
#         try:
#             self.loadBlocks()
#             try:
#                 bestHeight = max(self.blocks.keys())
#             except ValueError:
#                 logWrite("No saved blocks; loading from disk")
#             else:
#                 logWrite("Loading blocks, found best height at " + str(bestHeight))             
#         except IOError:
#             logWrite("Couldn't load saved blocks; loading from disk")
#         self.updateBlocks(currHeight=currHeight)

#     def updateBlocks(self, dummyArg=None, currHeight=None):
#         if not currHeight:
#             currHeight = proxy.getblockcount()
#         self.readFromHistory((currHeight-self.adaptive, currHeight))
#         self.adaptiveCalc(currHeight=currHeight)
#         try:
#             self.saveBlocks()
#         except IOError:
#             logWrite("Error saving blocks.")

#         # Temp debug stuff:
#         print("Number of blocks: " + str(len(self.blocks)))
#         print("Best block: " + str(max(self.blocks.keys())))
#         pprint(self.getStats())





    # def adaptiveCalc(self):
    #     if not self.adaptive:
    #         raise ModelError("Simulation is not in adaptive mode.")
    #     if len(self.blocks) < 2:
    #         raise ModelError("Too few blocks.")

    #     blockHeights = self.blocks.keys()
    #     blockHeights.sort()

    #     self.resetMetrics()
    #     prevHeight = blockHeights[0]
    #     self.nextBlock(self.blocks[prevBlock][0], self.blocks[prevBlock][1])

    #     for height in blockHeights[1:]:
    #         block = self.blocks[height]
    #         if height > prevHeight + 1:
    #             self.strandedBlocks = []
    #         self.nextBlock(block[0], block[1])
    #         prevHeight = height

    # def pushBlock(blockHeight, blockInterval, minFeeRate, currHeight=None):
    #     if self.adaptive:
    #         self.blocks[blockHeight] = (blockInterval, minFeeRate)
    #         heightThresh = currHeight - self.adaptive
    #         for height in self.blocks.keys():
    #             if height < heightThresh:
    #                 del self.blocks[height]
    #         self.adaptiveCalc()
    #     else:
    #         self.nextBlock(blockHeight, blockInterval, minFeeRate)


# def QEfromHistory(maxMFR, adaptive, blockHeightRange, dbFile=historyFile):
#     qe = QEstimator(maxMFR, adaptive)
#     prevBlock = None
#     idx = 0
#     heights = range(*blockHeightRange)
#     try:
#         while not prevBlock:
#             prevBlock = Block.blockFromHistory(heights[idx],dbFile=dbFile)
#             idx += 1
#     except IndexError:
#         raise ValueError("No valid blocks.")

#     for height in heights[idx:]:
#         if not (height % 10):
#             print(height)
#         block = Block.blockFromHistory(height)
#         if block:
#             if height == prevBlock.height + 1:
#                 blockInterval = block.time - prevBlock.time
#                 blockInterval = max(blockInterval, 1)
#                 try:
#                     minLeadTime = min([entry['leadTime'] for entry in 
#                         block.entries.itervalues() if entry['inBlock']])
#                 except ValueError:
#                     minLeadTime = 0
#                 blockStat = BlockStat(block,minLeadTime,bootstrap=False,allowZeroFee=True)
#                 minFeeRate = blockStat.calcFee().minFeeRate
#                 qe.blocks[height] = (blockInterval, minFeeRate)
#             prevBlock = block

#     qe.adaptiveCalc()
#     return qe
