from feemodel.config import saveBlocksFile, historyFile, config
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
    def __init__(self, maxMFR, adaptive=0):
        self.qMetrics = [FeeClass(i*feeResolution)
            for i in range(maxMFR // feeResolution + 1)]
        self.adaptive = adaptive
        self.maxMFR = maxMFR
        if adaptive:
            self.blocks = {}

    def pushBlock(self, blockHeight, blockInterval, minFeeRate):
        if not self.adaptive:
            for feeClass in self.qMetrics:
                feeClass.nextBlock(blockHeight, blockInterval, minFeeRate)
        else:
            raise ModelError("pushBlock not allowed in adaptive mode")

            # self.blocks[blockHeight] = (blockInterval, minFeeRate)
            # heightThresh = blockHeight - self.adaptive
            # for height in self.blocks.keys():
            #     if height < heightThresh:
            #         del self.blocks[height]

            # if not (blockHeight % adaptiveRefreshInterval):
            #     self.adaptiveCalc()
            #     try:
            #         self.saveBlocks()
            #     except (IOError, ValueError) as e:
            #         logWrite("Something went wrong trying to save blocks.")
            #         logWrite(str(e))

    def adaptiveCalc(self, currHeight=None):
        if not currHeight:
            currHeight = proxy.getblockcount()

        heightThresh = currHeight - self.adaptive
        for height in self.blocks.keys():
            if height < heightThresh:
                del self.blocks[height]

        self.qMetrics = [FeeClass(i*feeResolution)
            for i in range(self.maxMFR // feeResolution + 1)]
        blocksItems = self.blocks.items()
        blocksItems.sort(key=lambda x: x[0])
        for height, block in blocksItems:
            for feeClass in self.qMetrics:
                feeClass.nextBlock(height, block[0], block[1])

    def getStats(self):
        return [(fc.feeRate, fc.avgWait, fc.strandedProportion, fc.avgStrandedBlocks)
            for fc in self.qMetrics]

    def readFromHistory(self, blockHeightRange, dbFile=historyFile):
        try:
            bestHeight = max(self.blocks.keys())
        except ValueError:
            bestHeight = None
        
        prevBlock = None
        idx = 0
        heights = range(max(bestHeight,blockHeightRange[0]), blockHeightRange[1]+1)

        try:
            while not prevBlock:
                prevBlock = Block.blockFromHistory(heights[idx],dbFile=dbFile)
                idx += 1
        except IndexError:
            raise ValueError("No valid blocks.")

        for height in heights[idx:]:
            if not (height % 10):
                print(height)
            block = Block.blockFromHistory(height)
            if block:
                if height == prevBlock.height + 1:
                    blockInterval = block.time - prevBlock.time
                    blockInterval = max(blockInterval, 1)
                    try:
                        minLeadTime = min([entry['leadTime'] for entry in 
                            block.entries.itervalues() if entry['inBlock']])
                    except ValueError:
                        minLeadTime = 0
                    blockStat = BlockStat(block,minLeadTime,bootstrap=False,allowZeroFee=True)
                    minFeeRate = blockStat.calcFee().minFeeRate
                    self.blocks[height] = (blockInterval, minFeeRate)
                prevBlock = block

    def saveBlocks(self):
        if not self.blocks:
            raise ValueError("There's nothing to save.")

        with open(saveBlocksFile, 'wb') as f:
            pickle.dump(self.blocks, f)

    def loadBlocks(self, dbFile=saveBlocksFile):
        with open(dbFile, 'rb') as f:
            self.blocks = pickle.load(f)

    def __eq__(self, other):
        return all([
            self.qMetrics[idx] == other.qMetrics[idx]
            for idx in range(len(self.qMetrics))
        ])


class QEonline(QEstimator):
    def __init__(self, maxMFR, adaptive=adaptiveWindow, currHeight=None):
        super(QEonline, self).__init__(maxMFR, adaptive)
        try:
            self.loadBlocks()
            try:
                bestHeight = max(self.blocks.keys())
            except ValueError:
                logWrite("No saved blocks; loading from disk")
            else:
                logWrite("Loading blocks, found best height at " + str(bestHeight))             
        except IOError:
            logWrite("Couldn't load saved blocks; loading from disk")
        self.updateBlocks(currHeight=currHeight)

    def updateBlocks(self, dummyArg=None, currHeight=None):
        if not currHeight:
            currHeight = proxy.getblockcount()
        self.readFromHistory((currHeight-self.adaptive, currHeight))
        self.adaptiveCalc(currHeight=currHeight)
        try:
            self.saveBlocks()
        except IOError:
            logWrite("Error saving blocks.")

        # Temp debug stuff:
        print("Number of blocks: " + str(len(self.blocks)))
        print("Best block: " + str(max(self.blocks.keys())))
        pprint(self.getStats())


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
