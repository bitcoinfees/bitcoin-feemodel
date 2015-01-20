from feemodel.plotting import waitTimesGraph, ratesGraph, transWaitGraph
from feemodel.txmempool import TxMempool, LoadHistory
from feemodel.measurement import TxRates, TxSample, estimateBlockInterval, TxWaitTimes
from feemodel.pools import PoolEstimator, PoolEstimatorOnline
from feemodel.util import proxy, estimateVariance, logWrite, StoppableThread, pickle, Saveable, DataSample
from feemodel.util import interpolate
from feemodel.queue import QEstimator
from feemodel.config import config, saveSSFile, savePredictFile
from bitcoin.core import COIN
from collections import defaultdict
from random import expovariate
from copy import deepcopy, copy
from bisect import insort
import threading
from pprint import pprint
from time import time

# blockRate = config['simul']['blockRate'] # Once every 10 minutes
defaultBlockRate = 1./600
rateRatioThresh = 0.9
convergeThresh = 0.0001
predictionLevel = 0.9
predictionRetention = 2016
waitTimesWindow = 2016
maxTxSamples = 10000
transBlockIntervalWindow = 432
transRateIntervalLen = 18 # The number of recent blocks used to estimate tx rate for transient analysis
transMinRateTime = 3600
ssBlockIntervalWindow = 2016 # The number of blocks used to estimate block interval
ssRateIntervalLen = 2016 # the number of recent blocks used to estimate tx rate
ssMinRateTime = 3600*24 # Min amount of time needed to estimate tx rates for ss
ssMaxTxSamples = 100000
poolBlocksWindow = 2016
minFeeSpacing = 1000
#defaultFeeValues = tuple(range(0, 100000, 1000))
defaultFeeValues = tuple(range(0, 10000, 1000) + range(10000, 100000, 10000))
poolEstimatePeriod = 144 # Re-estimate pools every x blocks
ssPeriod = 144 # Re-simulate steady state every x blocks
txRateMultiplier = 1. # Simulate with tx rate multiplied by this factor

predictLock = threading.RLock()

class Simul(object):
    def __init__(self, pe, tr, blockRate=defaultBlockRate):
        self.pe = pe
        self.tr = tr
        self.feeClassValues = None
        self.blockRate = blockRate

    def initCalcs(self):
        self.poolmfrs, self.processingRate, self.processingRateUpper = self.pe.getProcessingRate(self.blockRate)
        self.txByteRate, self.txRate = self.tr.getByteRate(self.poolmfrs)
        self.agCap, self.exRate, self.poolCap = self.pe.calcCapacities(self.tr, self.blockRate)

        self.stableFeeRate = None
        for feeRate, cap in self.agCap:
            rateRatio = cap[0] / cap[1] if cap[1] else float("inf")
            if rateRatio <= rateRatioThresh:
                if not self.stableFeeRate:
                    self.stableFeeRate = feeRate
            else:
                self.stableFeeRate = None

        if not self.stableFeeRate:
            raise ValueError("The queue is not stable - arrivals exceed processing for all feerates.")
        # Remove the unstable fee classes here, instead of in queue.py
        self.feeClassValues = getFeeClassValues(self.poolmfrs, self.tr.txSamples, self.stableFeeRate)

    def transient(self, mempool, numiters=1000, stopFlag=None):
        self.initCalcs()
        self.initMempool(mempool)
        waitTimes = {feeRate: TransientWait() for feeRate in self.feeClassValues}
        txNoDeps = self.txNoDeps
        txDeps = self.txDeps

        starttime = time()
        for i in range(numiters):
            if stopFlag and stopFlag.is_set():
                raise ValueError("transient simulation terminated.")
            stranded = self.feeClassValues[:]
            self.txNoDeps = txNoDeps[:]
            self.txDeps = {txid: {'tx': txDeps[txid]['tx'], 'depends': txDeps[txid]['depends'][:]}
                for txid in txDeps}
            totaltime = 0.
            while stranded:
                t = self.addToMempool()
                sfr = self.processBlock()
                totaltime += t
                strandedDel = []
                for feeRate in stranded:
                    if feeRate >= sfr:
                        waitTimes[feeRate].addSample(totaltime)
                        strandedDel.append(feeRate)
                for feeRate in strandedDel:
                    stranded.remove(feeRate)

        elapsedtime = time() - starttime

        for wt in waitTimes.values():
            wt.calcStats()

        return sorted(waitTimes.items(), key=lambda x: x[0]), elapsedtime

    def steadyState(self, miniters=10000, maxiters=1000000, maxtime=3600, mempool=None, stopFlag=None):
        self.initCalcs()
        if not mempool:
            mempool = {}
        self.initMempool(mempool)

        q = QEstimator(self.feeClassValues)
        # queue statistics from an aggregate of shorter run lengths
        # for comparing against measured wait times
        shortstats = {feeRate: DataSample() for feeRate in self.feeClassValues}
        qw = QEstimator(self.feeClassValues)
        starttime = time()
        for i in range(maxiters):
            if stopFlag and stopFlag.is_set():
                raise ValueError("Simulation terminated.")
            elapsedtime = time() - starttime
            if elapsedtime > maxtime:
                break
            t = self.addToMempool()
            sfr = self.processBlock()
            d = q.nextBlock(i, t, sfr)
            qw.nextBlock(i, t, sfr)
            if not (i+1) % waitTimesWindow:
                for fc in qw.qMetrics:
                    shortstats[fc.feeRate].addSample(fc.avgWait)
                qw = QEstimator(self.feeClassValues)

        i += 1
        if i < miniters:
            raise ValueError("Too few iterations in the allotted time.")
        #print("Num iters: %d" % i)
        for stat in shortstats.values():
            stat.calcStats()
        shorterrs = [(feeRate, stat.std*1.96) for feeRate, stat in shortstats.items()]
        shorterrs.sort(key=lambda x: x[0])
        return q.getStats(), shorterrs, elapsedtime, i

    def addToMempool(self):
        t = expovariate(self.blockRate)
        txSample = self.tr.generateTxSample(t*self.txRate*txRateMultiplier)
        self.txNoDeps.extend([tx for tx in txSample if tx.feeRate >= self.stableFeeRate])
        self.txNoDeps.sort(key=lambda x: x.feeRate)

        return t

    def initMempool(self, mempool):
        self.txNoDeps = []
        self.txDeps = {}
        self.depMap = defaultdict(list)

        for txid, entry in mempool.items():
            if not 'feeRate' in entry:
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']
            if not entry['depends']:
                self.txNoDeps.append(TxSample(txid, entry['size'], entry['feeRate']))
            else:
                for dep in entry['depends']:
                    self.depMap[dep].append(txid)
                self.txDeps[txid] = {'tx': TxSample(txid, entry['size'], entry['feeRate']),
                    'depends': entry['depends']}

        self.txNoDeps.sort(key=lambda x: x.feeRate)

    def processBlock(self, info=None):
        poolName, maxBlockSize, minFeeRate = self.pe.selectRandomPool()

        blockSize = 0
        strandingFeeRate = float("inf")
        blockSizeLimited = 0

        rejectedTx = []
        while self.txNoDeps:
            # We need to change this to get better stranding fr for size limited blocks. Done.
            newTx = self.txNoDeps.pop()
            if newTx.feeRate >= minFeeRate:
                if newTx.size + blockSize <= maxBlockSize:
                    if blockSizeLimited > 0:
                        blockSizeLimited -= 1
                    else:
                        strandingFeeRate = min(newTx.feeRate, strandingFeeRate)

                    blockSize += newTx.size

                    dependants = self.depMap.get(newTx.txid)
                    if dependants:
                        for txid in dependants:
                            entry = self.txDeps[txid]
                            entry['depends'].remove(newTx.txid)
                            if not entry['depends']:
                                insort(self.txNoDeps, entry['tx'])
                else:
                    rejectedTx.append(newTx)
                    blockSizeLimited += 1
            else:
                rejectedTx.append(newTx)
                break

        self.txNoDeps.extend(rejectedTx)
        if info:
            info['poolName'] = poolName
        return strandingFeeRate if blockSizeLimited else minFeeRate


class SimulOnline(TxMempool):
    def __init__(self):
        self.waitTimes = ()
        self.pools = []
        self.steadyStats = []
        self.transientStats = []
        self.predictScores = {}
        self.processLock = threading.Lock()
        self.dataLock = threading.Lock()
        try:
            self.pe = PoolEstimator.loadObject()
        except IOError:
            logWrite("Unable to load poolEstimator")
            self.pe = PoolEstimator(poolBlocksWindow)

        self.peo = PoolEstimatorOnline(self.pe, poolEstimatePeriod)
        logWrite("Updating pool estimates..")
        self.peo.updateEstimates()
        logWrite("Done.")

        self.steadySim = SteadyStateSim(self.pe)
        self.transientSim = TransientSim(self.pe, self)

        self.predictions = Predictions(self.transientSim.tStats, defaultFeeValues, predictionRetention)
        self.predictions.loadData()

        super(SimulOnline, self).__init__()

    def update(self):
        self.updateData()
        super(SimulOnline, self).update()
        self.predictions.updatePredictions(self.getMempool())

    def processBlocks(self, *args, **kwargs):
        with self.processLock:
            blocks = super(SimulOnline, self).processBlocks(*args, **kwargs)
            self.predictions.pushBlocks(blocks)
            self.predictions.saveData()

    def run(self):
        with self.peo.threadStart(), self.steadySim.threadStart(), self.transientSim.threadStart():
            super(SimulOnline, self).run()
            logWrite("Stopping SimulOnline...")
        self.predictions.saveData()
        logWrite("Done. SimulOnline finished.")

    def updateData(self):
        self.updateSingle('waitTimes', self.steadySim.getWaitTimes)
        self.updateSingle('pools', self.pe.getPools)
        self.updateSingle('steadyStats', self.steadySim.getStats)
        self.updateSingle('transientStats', self.transientSim.getStats)
        self.updateSingle('predictScores', self.predictions.getScore)

    def updateSingle(self, attr, targetFn):
        try:
            with self.dataLock:
                setattr(self, attr, targetFn())
        except ValueError:
            # Have to find some way to indicate the status, when first loading up.
            pass

    def getWaitTimes(self):
        with self.dataLock:
            return self.waitTimes

    def getSteadyStats(self):
        with self.dataLock:
            return self.steadyStats

    def getTransientStats(self):
        with self.dataLock:
            return self.transientStats

    def getPools(self):
        with self.dataLock:
            return self.pools

    def getPredictions(self):
        with self.dataLock:
            return self.predictScores


class SteadyStateSim(StoppableThread):
    '''Simulate steady-state every <ssPeriod> blocks'''
    def __init__(self, pe, ssPeriod=ssPeriod):
        self.ssPeriod = ssPeriod
        self.pe = pe
        self.statLock = threading.Lock()
        self.status = 'idle'
        try:
            self.loadStats()
        except:
            logWrite("SS: Unable to load saved stats - starting from scratch.")
            self.statsCache = (0, {})
            self.waitTimesCache = []

        super(SteadyStateSim, self).__init__()

    def run(self):
        logWrite("Starting steady-state sim.")
        while not self.isStopped():
            self.simulate()
            self.sleep(600)
        logWrite("Closed up steady-state sim.")

    def simulate(self):
        bestHeight = self.statsCache[0]
        currHeight = proxy.getblockcount()
        if currHeight - bestHeight <= self.ssPeriod:
            return

        pe = self.pe.copyObject()
        blockRateStat = estimateBlockInterval((currHeight-ssBlockIntervalWindow+1, currHeight+1))
        try:
            tr = TxRates.loadObject()
        except:
            logWrite("Unable to load txRates, calculating from scratch.")
            tr = TxRates(maxSamples=ssMaxTxSamples, minRateTime=ssMinRateTime)

        sim = Simul(pe, tr, blockRate=1./blockRateStat[0])

        try:
            self.status = 'running'
            # We originally did a separate interval check here to avoid doing tr calc rates when
            # we wanted to redo steady sim.
            #if currHeight - tr.bestHeight > self.ssPeriod:
            logWrite("Starting SS tr.calcRates")
            tr.calcRates((currHeight-ssRateIntervalLen+1, currHeight+1), stopFlag=self.getStopObject())
            logWrite("Finished SS tr.calcRates")
            tr.saveObject()

            stats, shorterrs, timespent, numiters = sim.steadyState(maxiters=100000,stopFlag=self.getStopObject())

            wt = TxWaitTimes(sim.feeClassValues, waitTimesWindow=waitTimesWindow)
            currHeight = proxy.getblockcount()
            heightRange = (currHeight-waitTimesWindow+1, currHeight+1)
            lh = LoadHistory()
            lh.registerFn(lambda blocks: wt.pushBlocks(blocks, init=True), heightRange)
            lh.loadBlocks()
            wt.calcWaitTimes()
        except ValueError as e:
            logWrite("SteadyStateSim error:")
            logWrite(str(e))
        else:
            logWrite("ss simul completed with time %.1f seconds and %d iterations." % (
                timespent, numiters))
            currHeight = proxy.getblockcount()
            qstats = {
                    'stats': [(stat.feeRate, stat.avgWait, stat.strandedProportion, stat.avgStrandedBlocks)
                        for stat in stats],
                    'txByteRate': sim.txByteRate,
                    'txRate': sim.txRate,
                    'blockRate': blockRateStat,
                    'poolmfrs': sim.poolmfrs,
                    'processingRate': sim.processingRate,
                    'processingRateUpper': sim.processingRateUpper,
                    'stableFeeRate': sim.stableFeeRate,
                    'timespent': timespent,
                    'numiters': numiters,
                    'shorterrs': shorterrs
            }
            with self.statLock:
                self.waitTimesCache = wt.getWaitTimes()
                self.statsCache = (currHeight, qstats)
                try:
                    self.saveStats()
                except IOError:
                    logWrite("Unable to save ss stats.")
            self.updatePlotly()
        finally:
            self.status = 'idle'

    def getStats(self):
        with self.statLock:
            return self.statsCache

    def getWaitTimes(self):
        with self.statLock:
            return self.waitTimesCache

    def saveStats(self):
        with open(saveSSFile, 'wb') as f:
            pickle.dump((self.statsCache, self.waitTimesCache), f)

    def loadStats(self):
        with open(saveSSFile, 'rb') as f:
            self.statsCache, self.waitTimesCache = pickle.load(f)

    def updatePlotly(self, async=True):
        feeClasses = self.statsCache[1]['poolmfrs']
        procrate = [r*600 for r in self.statsCache[1]['processingRate']]
        procrateUpper = [r*600 for r in self.statsCache[1]['processingRateUpper']]
        txByteRate = [r*600 for r in self.statsCache[1]['txByteRate']]
        stableFeeRate = self.statsCache[1]['stableFeeRate']
        stableStat = (stableFeeRate, txByteRate[feeClasses.index(stableFeeRate)])
        t = threading.Thread(
                             target=ratesGraph.updateAll,
                             args=(feeClasses,procrate,procrateUpper,txByteRate,stableStat)
                            )
        t.start()
        if not async:
            t.join()
        x = [stat[0] for stat in self.statsCache[1]['stats']]
        steady_y = [stat[1] for stat in self.statsCache[1]['stats']]
        measured_y = [w[1][0] for w in self.waitTimesCache[0]]
        m_error = [stat[1] for stat in self.statsCache[1]['shorterrs']]
        t = threading.Thread(
                             target=waitTimesGraph.updateSteadyState,
                             args=(x, steady_y, measured_y, m_error)
                            )
        t.start()
        if not async:
            t.join()



class TransientSim(StoppableThread):
    '''Constantly simulate transient behavior'''
    def __init__(self, pe, mempool):
        self.pe = pe
        self.mempool = mempool
        self.tr = TxRates(minRateTime=transMinRateTime, maxSamples=maxTxSamples)
        self.simLock = threading.Lock()
        self.statLock = threading.Lock()
        self.qstats = {}
        self.tStats = TransientStats()
        super(TransientSim, self).__init__()

    def run(self):
        logWrite("Starting transient sim.")
        while not self.isStopped():
            with self.simLock:
                pass
            threading.Thread(target=self.simulate, name='transient-sim').start()
            self.sleep(60)
        for thread in threading.enumerate():
            if thread.name == 'transient-sim':
                thread.join()
        logWrite("Closed up transient sim.")

    def simulate(self):
        with self.simLock:
            currHeight = proxy.getblockcount()
            blockRateStat = estimateBlockInterval((currHeight-transBlockIntervalWindow+1, currHeight+1))
            sim = Simul(deepcopy(self.pe), self.tr, blockRate=1./blockRateStat[0])
            try:
                if currHeight > self.tr.bestHeight:
                    logWrite("Starting tr.calcRates")
                    # Problem with range (339006-18+1,339006+1)
                    self.tr.calcRates((currHeight-transRateIntervalLen+1, currHeight+1),
                        stopFlag=self.getStopObject())
                    logWrite("Finished tr.calcRates")
                mapTx = self.mempool.getMempool()
                waitTimes, timespent = sim.transient(mapTx, stopFlag=self.getStopObject())
            except ValueError as e:
                logWrite("TransientSim error:")
                logWrite(e.message)
            else:
                logWrite("transient simul completed with time %.1f seconds." % timespent)
                with self.statLock:
                    self.qstats = {
                        'stats': [(feeRate, tw.getStats()) for feeRate, tw in waitTimes],
                        'txByteRate': sim.txByteRate,
                        'txRate': sim.txRate,
                        'blockRate': blockRateStat,
                        'poolmfrs': sim.poolmfrs,
                        'processingRate': sim.processingRate,
                        'processingRateUpper': sim.processingRateUpper,
                        'stableFeeRate': sim.stableFeeRate,
                        'timespent': timespent,
                        'mempoolSize': getMempoolSize(mapTx, sim.poolmfrs)
                    }
                self.tStats.update(waitTimes, timespent, sim)
                self.updatePlotly()

    def getStats(self):
        with self.statLock:
            return self.qstats

    def updatePlotly(self):
        x = [stat[0] for stat in self.qstats['stats']]
        y = [stat[1][0] for stat in self.qstats['stats']]
        err = [stat[1][0]-stat[1][2][0] for stat in self.qstats['stats']]
        t = threading.Thread(
                             target=transWaitGraph.updateAll,
                             args=(x,y,err)
                            )
        t.start()


class TransientStats(object):
    # To-do - assign transientStats object in TransientSim
    def __init__(self):
        self.waitTimes = None
        self.timespent = None
        self.sim = None
        self.lock = threading.Lock()

    def update(self, waitTimes, timespent, sim):
        with self.lock:
            self.waitTimes = waitTimes
            self.timespent = timespent
            self.sim = sim
            self.px = [w[0] for w in self.waitTimes]
            self.py = [w[1].predictionInterval for w in self.waitTimes]
            self.ax = self.px[-1::-1]
            self.ay = sorted([w[1].mean for w in self.waitTimes])

    # Use interpolation here.
    def predictConf(self, entry):
        entryFeeRate = entry.get('feeRate')
        if entryFeeRate is None:
            entryFeeRate = int(entry['fee']*COIN) * 1000 // entry['size']
        with self.lock:
            if not self.waitTimes:
                return None
            predictionInterval, idx = interpolate(entryFeeRate, self.px, self.py)
            if idx == 0:
                return None
            else:
                return predictionInterval + entry['time']

    def inverseAvgConf(self, confTime):
        ''' inverseAvgConf(self, confTime) - Return, by linear interpolation,
            the feerate for a given avg confirmation time.'''
        with self.lock:
            if not self.waitTimes:
                return None
            feeRate, idx = interpolate(confTime, self.ay, self.ax)
            if idx == 0:
                return None
            else:
                return feeRate


# Change this to subclass DataSample.. Done
class TransientWait(DataSample):
    def calcStats(self):
        super(self.__class__, self).calcStats()
        self.predictionInterval = self.getPercentile(0.9)

    def getStats(self):
        return (self.mean, self.std, self.meanInterval, self.predictionInterval)

    def __repr__(self):
        return "TW{mean: %.2f, std: %.2f, mean95conf: (%.2f, %.2f), pred%d: %.2f}" % (
            self.mean, self.std, self.meanInterval[0],
            self.meanInterval[1], int(predictionLevel*100), self.predictionInterval)


def getMempoolSize(mapTx, feeValues):
    mempoolSize = [
        sum([entry['size'] for entry in mapTx.values() if entry['feeRate'] >= feeValue])
        for feeValue in feeValues
    ]
    return mempoolSize

def getFeeClassValues(poolmfrs, txSamples, stableFeeRate):
    txs = [(tx.size, tx.feeRate) for tx in txSamples]
    txs.sort(key=lambda x: x[1])
    totalBytes = sum([tx[0] for tx in txs])
    q = 0.05
    byteLevel = q*totalBytes
    currTotal = 0
    feeValues = set()
    txi = iter(txs)
    while byteLevel <= (1-q)*totalBytes:
        while currTotal < byteLevel:
            tx = txi.next()
            currTotal += tx[0]
        feeValues.add(tx[1])
        byteLevel += q*totalBytes

#    print("Debug - feeValues from samples:")
#    print(sorted(feeValues))
    feeValues = list(feeValues)
    feeValues.extend(poolmfrs)
    feeValues.sort(reverse=True)

    prevFeeRate = feeValues[0]
    feeClassValues = [prevFeeRate]
    for feeRate in feeValues[1:]:
        if feeRate < stableFeeRate:
            break
        if prevFeeRate - feeRate >= minFeeSpacing:
            feeClassValues.append(feeRate)
            prevFeeRate = feeRate

    feeClassValues.sort()
    return feeClassValues


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







#class MempoolSize(CircularBuffer):
#    def pushBlocks(blocks):
#        for block in blocks:
#            if block:
#                mempoolSize = sum([entry['size'] for entry in block.entries.itervalues()
#                    if not entry['inBlock']])
#                self.pushData(block.height, mempoolSize)





