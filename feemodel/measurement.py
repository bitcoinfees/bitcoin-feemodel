from feemodel.util import proxy, logWrite
from feemodel.config import config, saveWaitFile

try:
    import cPickle as pickle
except ImportError:
    import pickle

feeResolution = config['queue']['feeResolution']
priorityThresh = config['measurement']['priorityThresh']

class WaitMeasure(object):
    def __init__(self, maxMFR, adaptive, loadFile=saveWaitFile):
        self.adaptive = adaptive
        self.blockData = {}
        self.maxMFR = maxMFR
        self.waitTimes = None
        self.bestHeight = None

        if loadFile:
            try:
                self.loadBlockData()
            except IOError:
                logWrite("WM: Couldn't load saved measurements; loading from disk.")
            else:
                logWrite("WM: Saved measurements loaded: %s" % (repr(self),))

    def getStats(self):
        if self.waitTimes:
            return [repr(self)] + [(idx*feeResolution,) + t
                for idx,t in enumerate(self.waitTimes)]
        else:
            return []

    def pushBlocks(self, blocks, isInit=False):
        for block in blocks:
            if not block:
                continue
            self.blockData[block.height] = WaitMeasureBlock(self.maxMFR)
            self.bestHeight = block.height
            numTxs = 0
            for entry in block.entries.itervalues():
                if (entry['inBlock']
                    and entry['feeRate'] < (self.maxMFR + feeResolution)
                    and not entry['depends']
                    and entry['currentpriority'] < priorityThresh):
                        self.blockData[block.height].addTx(
                            entry['feeRate'], block.time - entry['time'])
                        numTxs += 1
            logWrite("WM: Added block %d with %d transactions" %
                        (block.height, numTxs))
        if not isInit:
            self.adaptiveCalc()
            try:
                self.saveBlockData()
            except IOError:
                logWrite("WM: Couldn't save block data.")

    def adaptiveCalc(self):
        if not self.bestHeight:
            raise ValueError("WM: Empty block data.")

        heightThresh = self.bestHeight - self.adaptive
        for height in self.blockData.keys():
            if height < heightThresh:
                del self.blockData[height]

        self.waitTimes = [None] * (self.maxMFR // feeResolution + 1)
        for idx in range(self.maxMFR // feeResolution + 1):
            totalTxs = sum([wmBlock.avgWaitTimes[idx][0]
                for wmBlock in self.blockData.itervalues()])
            if totalTxs:
                self.waitTimes[idx] = (sum([wmBlock.avgWaitTimes[idx][0]*wmBlock.avgWaitTimes[idx][1]
                    for wmBlock in self.blockData.itervalues()]) / totalTxs, totalTxs)
            else:
                self.waitTimes[idx] = (-1, totalTxs)

        # self.waitTimes = [
        #     sum([wmBlock.avgWaitTimes[idx][0]*wmBlock.avgWaitTimes[idx][1]
        #         for wmBlock in self.blockData.itervalues()]) /
        #     sum([wmBlock.avgWaitTimes[idx][0]
        #         for wmBlock in self.blockData.itervalues()])
        #     for idx in range(self.maxMFR // feeResolution + 1)
        # ]

    def saveBlockData(self, dbFile=saveWaitFile):
        with open(dbFile, 'wb') as f:
            pickle.dump(self.blockData,f)

    def loadBlockData(self, dbFile=saveWaitFile):
        with open(dbFile, 'rb') as f:
            self.blockData = pickle.load(f)
        try:
            self.bestHeight = max(self.blockData)
            self.maxMFR = (len(self.blockData[self.bestHeight].avgWaitTimes)-1)*feeResolution
        except ValueError:
            self.bestHeight = None

    def __repr__(self):
        return "WM{maxMFR: %d, adaptive: %d, bestHeight: %d, numBlocks: %d}" % (
            self.maxMFR, self.adaptive, self.bestHeight if self.bestHeight else -1, len(self.blockData))


class WaitMeasureBlock(object):
    def __init__(self, maxMFR):
        self.avgWaitTimes = [(0, 0.)]*(maxMFR // feeResolution + 1)

    def addTx(self, feeRate, waitTime):
        feeClassIdx = feeRate // feeResolution
        numTxs, prevWaitTime = self.avgWaitTimes[feeClassIdx]
        self.avgWaitTimes[feeClassIdx] = (numTxs+1,
            (prevWaitTime*numTxs + waitTime) / (numTxs + 1))