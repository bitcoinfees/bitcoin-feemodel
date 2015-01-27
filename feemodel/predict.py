predictLock = threading.RLock()

class CircularBuffer(object):
    def __init__(self, retention):
        self.retention = retention
        self.data = {}

    def pushData(self, key, val):
        self.data[key] = val
        thresh = key - self.retention
        for k in self.data.keys():
            if k <= thresh:
                del self.data[k]

    def getDataIter(self):
        return self.data.iteritems()

    def getBestHeight(self):
        if self.data:
            return max(self.data)
        else:
            return None


class BlockPrediction(object):
    def __init__(self, feeClassValues):
        #feeClassValues assumed sorted
        self.scores = [(feeRate, [0, 0]) for feeRate in feeClassValues]

    def addScore(self, feeRate, isIn):
        validFeeRate = False
        for feeClass, score in reversed(self.scores):
            if feeClass < feeRate:
                validFeeRate = True
                break
        if validFeeRate:
            score[1] += 1
            if isIn:
                score[0] += 1


class Predictions(CircularBuffer):
    def __init__(self, transientStats, feeClassValues, retention, saveFile=savePredictFile):
        self.predictions = {}
        self.feeClassValues = feeClassValues
        self.transientStats = transientStats
        self.totalScores = {feeRate: [0, 0] for feeRate in self.feeClassValues}
        self.saveFile = saveFile

        super(Predictions, self).__init__(retention)

    def updatePredictions(self, mapTx):
        with predictLock:
            newTxids = set(mapTx) - set(self.predictions)
            for txid in newTxids:
                entry = mapTx[txid]
                if not entry['depends']:
                    self.predictions[txid] = self.transientStats.predictConf(entry)
                else:
                    self.predictions[txid] = None

    def pushBlocks(self, blocks):
        with predictLock:
            for block in blocks:
                if not block:
                    return
                numPredicts = 0 # Debug
                blockPredict = BlockPrediction(self.feeClassValues)
                for txid, entry in block.entries.iteritems():
                    if entry['inBlock']:
                        predictedConfTime = self.predictions.get(txid)
                        if predictedConfTime:
                            isIn = predictedConfTime > block.time
                            blockPredict.addScore(entry['feeRate'], isIn)
                            del self.predictions[txid]
                            numPredicts += 1 # Debug
                self.pushData(block.height, blockPredict)
                print("In block %d, %d predicts tallied." % (block.height, numPredicts)) # Debug

                delPredictions = set(self.predictions) - set(block.entries)
                for txid in delPredictions:
                    del self.predictions[txid]

            self.calcScore()

    def calcScore(self):
        with predictLock:
            self.totalScores = {feeRate: [0, 0] for feeRate in self.feeClassValues}
            for dummy, blockPredict in self.getDataIter():
                for feeClass, score in blockPredict.scores:
                    self.totalScores[feeClass][0] += score[0]
                    self.totalScores[feeClass][1] += score[1]

    def getScore(self):
        with predictLock:
            return self.totalScores

    def saveData(self):
        with predictLock, open(self.saveFile, 'wb') as f:
            pickle.dump((self.predictions, self.data), f)

    def loadData(self):
        try:
            with open(self.saveFile, 'rb') as f:
                self.predictions, self.data = pickle.load(f)
        except:
            logWrite("Unable to load predict data.")
        else:
            logWrite("%d predictions and %d blocks of predictscores." %
                (len(self.predictions),len(self.data)))

        for dummy, blockPredict in self.getDataIter():
            feeRates = tuple([feeRate for feeRate, score in blockPredict.scores])
            if feeRates != self.feeClassValues:
                logWrite("Mismatch in saved predictions feeclassvalues.")
                self.data = {}
                break
