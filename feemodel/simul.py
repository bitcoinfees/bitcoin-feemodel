from feemodel.txmempool import TxMempool, LoadHistory
from feemodel.measurement import TxRates, TxSample, estimateBlockInterval, TxWaitTimes
from feemodel.pools import PoolEstimator, PoolEstimatorOnline
from feemodel.util import proxy, estimateVariance, logWrite, StoppableThread, pickle
from feemodel.queue import QEstimator
from feemodel.config import config, saveSSFile
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
waitTimesWindow = 2016
maxTxSamples = 1000
transBlockIntervalWindow = 432
transRateIntervalLen = 18 # The number of recent blocks used to estimate tx rate for transient analysis
transMinRateTime = 3600
ssBlockIntervalWindow = 2016 # The number of blocks used to estimate block interval
ssRateIntervalLen = 2016 # the number of recent blocks used to estimate tx rate
ssMinRateTime = 3600*24 # Min amount of time needed to estimate tx rates for ss
poolBlocksWindow = 2016
minFeeSpacing = 500
defaultFeeValues = tuple(range(0, 100000, 1000))
poolEstimatePeriod = 144 # Re-estimate pools every x blocks
ssPeriod = 144 # Re-simulate steady state every x blocks
txRateMultiplier = 1. # Simulate with tx rate multiplied by this factor

class Simul(object):
    def __init__(self, pe, tr, blockRate=defaultBlockRate):
        self.pe = pe
        self.tr = tr
        self.feeClassValues = None
        self.blockRate = blockRate

    def initCalcs(self):
        self.poolmfrs, self.processingRate, self.processingRateUpper = self.pe.getProcessingRate(self.blockRate)
        self.txByteRate, self.txRate = self.tr.getByteRate(self.poolmfrs)

        self.stableFeeRate = None
        for idx in range(len(self.poolmfrs)):
            if self.txByteRate[idx]*txRateMultiplier / self.processingRate[idx] < rateRatioThresh:
                self.stableFeeRate = self.poolmfrs[idx]
                break
        if not self.stableFeeRate:
            raise ValueError("The queue is not stable - arrivals exceed processing for all feerates.")
        # Remove the unstable fee classes here, instead of in queue.py
        self.feeClassValues = getFeeClassValues(self.poolmfrs, self.stableFeeRate)

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
                        waitTimes[feeRate].addWait(totaltime)
                        strandedDel.append(feeRate)
                for feeRate in strandedDel:
                    stranded.remove(feeRate)

        elapsedtime = time() - starttime

        for wt in waitTimes.values():
            wt.calcStats()

        return sorted(waitTimes.items()), elapsedtime

    def steadyState(self, miniters=10000, maxiters=1000000, maxtime=3600, mempool=None, stopFlag=None):
        self.initCalcs()
        if not mempool:
            mempool = {}
        self.initMempool(mempool)

        q = QEstimator(self.feeClassValues)
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
        i += 1
        if i < miniters:
            raise ValueError("Too few iterations in the allotted time.")
        #print("Num iters: %d" % i)
        return q.getStats(), elapsedtime, i

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

    def processBlock(self):
        maxBlockSize, minFeeRate = self.pe.selectRandomPool()

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
        return strandingFeeRate if blockSizeLimited else minFeeRate


class SimulOnline(TxMempool):
    def __init__(self):
        self.waitTimes = ()
        self.pools = []
        self.steadyStats = []
        self.processLock = threading.Lock()
        self.dataLock = threading.Lock()
        try:
            self.pe = PoolEstimator.loadObject()
        except IOError:
            logWrite("Unable to load poolEstimator")
            self.pe = PoolEstimator(poolBlocksWindow)
        try:
            self.wt = TxWaitTimes.loadObject()
        except IOError:
            logWrite("Unable to load txWaitTimes.")
            self.wt = TxWaitTimes(defaultFeeValues, waitTimesWindow)

        self.peo = PoolEstimatorOnline(self.pe, poolEstimatePeriod)
        logWrite("Updating pool estimates..")
        self.peo.updateEstimates()
        logWrite("Done.")

        wtBestHeight = self.wt.getBestHeight()
        currHeight = proxy.getblockcount()
        wtBlockHeightRange = (max(wtBestHeight, currHeight-waitTimesWindow)+1, currHeight+20)

        logWrite("Loading TxWaitTimes estimator...")
        lh = LoadHistory()
        lh.registerFn(lambda blocks: self.wt.pushBlocks(blocks, init=True), wtBlockHeightRange)
        lh.loadBlocks()
        logWrite("Done.")

        self.wt.calcWaitTimes()
        self.wt.saveObject()

        self.steadySim = SteadyStateSim(self.pe)
        self.transientSim = TransientSim(self.pe, self)

        super(SimulOnline, self).__init__()

    def update(self):
        self.updateData()
        super(SimulOnline, self).update()

    def processBlocks(self, *args, **kwargs):
        with self.processLock:
            blocks = super(SimulOnline, self).processBlocks(*args, **kwargs)
            self.wt.pushBlocks(blocks)
            self.wt.saveObject()

    def run(self):
        with self.peo.threadStart(), self.steadySim.threadStart(), self.transientSim.threadStart():
            super(SimulOnline, self).run()
            logWrite("Stopping SimulOnline...")
        logWrite("Done. SimulOnline finished.")

    def updateData(self):
        self.updateSingle('waitTimes', self.wt.getWaitTimes)
        self.updateSingle('pools', self.pe.getPools)
        self.updateSingle('steadyStats', self.steadySim.getStats)
        self.updateSingle('transientStats', self.transientSim.getStats)

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


class SteadyStateSim(StoppableThread):
    '''Simulate steady-state every <ssPeriod> blocks'''
    def __init__(self, pe, ssPeriod=ssPeriod):
        self.ssPeriod = ssPeriod
        self.pe = pe
        self.statLock = threading.Lock()
        self.status = 'idle'
        try:
            self.loadStats()
        except IOError:
            logWrite("SS: Unable to load saved stats - starting from scratch.")
            self.statsCache = (0, {})

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
        except IOError:
            logWrite("Unable to load txRates, calculating from scratch.")
            tr = TxRates(maxSamples=maxTxSamples, minRateTime=ssMinRateTime)

        sim = Simul(pe, tr, blockRate=1./blockRateStat[0])

        try:
            self.status = 'running'
            if currHeight - tr.bestHeight > self.ssPeriod:
                logWrite("Starting tr.calcRates")
                tr.calcRates((currHeight-ssRateIntervalLen+1, currHeight+1), stopFlag=self.getStopObject())
                logWrite("Finished tr.calcRates")
                tr.saveObject()
            stats, timespent, numiters = sim.steadyState(maxiters=100000,stopFlag=self.getStopObject())
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
                    'numiters': numiters
            }
            with self.statLock:
                self.statsCache = (currHeight, qstats)
                try:
                    self.saveStats()
                except IOError:
                    logWrite("Unable to save ss stats.")
        finally:
            self.status = 'idle'

    def getStats(self):
        with self.statLock:
            return self.statsCache

    def saveStats(self):
        with open(saveSSFile, 'wb') as f:
            pickle.dump(self.statsCache, f)

    def loadStats(self):
        with open(saveSSFile, 'rb') as f:
            self.statsCache = pickle.load(f)

class TransientSim(StoppableThread):
    '''Constantly simulate transient behavior'''
    def __init__(self, pe, mempool):
        self.pe = pe
        self.mempool = mempool
        self.tr = TxRates(minRateTime=transMinRateTime, maxSamples=maxTxSamples)
        self.simLock = threading.Lock()
        self.statLock = threading.Lock()
        self.qstats = {}
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
            sim = Simul(self.pe, self.tr, blockRate=1./blockRateStat[0])
            try:
                if currHeight > self.tr.bestHeight:
                    logWrite("Starting tr.calcRates")
                    self.tr.calcRates((currHeight-transRateIntervalLen+1, currHeight+1))
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

    def getStats(self):
        with self.statLock:
            return self.qstats


class TransientWait(object):
    def __init__(self):
        self.waitTimes = []

    def addWait(self, waitTime):
        self.waitTimes.append(waitTime)

    def calcStats(self):
        self.waitTimes.sort()
        n = len(self.waitTimes)
        self.mean = float(sum(self.waitTimes)) / n
        self.variance = estimateVariance(self.waitTimes, self.mean)
        self.std = self.variance**0.5

        halfInterval = 1.96*(self.variance/n)**0.5
        self.meanInterval = (self.mean - halfInterval, self.mean + halfInterval) # 95% confidence interval
        self.predictionInterval = self.waitTimes[max(int(predictionLevel*n) - 1, 0)]

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

def getFeeClassValues(poolmfrs, stableFeeRate, feeValues=defaultFeeValues):
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

    return feeClassValues


#class CircularBuffer(Saveable):
#    def __init__(self, retention, saveFile):
#        self.retention = retention
#        self.data = {}
#        super(CircularBuffer, self).__init__(saveFile)
#
#    def pushData(self, key, val):
#        self.data[key] = val
#        thresh = key - self.retention
#        for k in self.data.keys():
#            if k <= thresh:
#                del self.data[k]
#
#    def getData(self):
#        return self.data
#
#    def getBestHeight(self):
#        if self.data:
#            return max(self.data)
#        else:
#            return None
#
#
#class MempoolSize(CircularBuffer):
#    def pushBlocks(blocks):
#        for block in blocks:
#            if block:
#                mempoolSize = sum([entry['size'] for entry in block.entries.itervalues()
#                    if not entry['inBlock']])
#                self.pushData(block.height, mempoolSize)





