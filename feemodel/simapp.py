from feemodel.plotting import waitTimesGraph, transWaitGraph, capsGraph, confTimeGraph
from feemodel.txmempool import TxMempool, LoadHistory
from feemodel.measurement import TxRates, TxSample, estimateBlockInterval, TxWaitTimes
from feemodel.pools import PoolEstimator, PoolEstimatorOnline
from feemodel.util import interpolate
from feemodel.util import proxy, estimateVariance, logWrite, StoppableThread, pickle, Saveable, DataSample
from feemodel.config import config, saveSSFile, savePredictFile
from copy import deepcopy, copy

predictionRetention = 2016
waitTimesWindow = 2016
maxTxSamples = 10000
transBlockIntervalWindow = 432
transRateIntervalLen = 9 # The number of recent blocks used to estimate tx rate for transient analysis
transMinRateTime = 600
ssBlockIntervalWindow = 2016 # The number of blocks used to estimate block interval
ssRateIntervalLen = 2016 # the number of recent blocks used to estimate tx rate
ssMinRateTime = 3600*24 # Min amount of time needed to estimate tx rates for ss
ssMaxTxSamples = 100000
ssPeriod = 72 # Re-simulate steady state every x blocks
poolEstimatePeriod = 144 # Re-estimate pools every x blocks

defaultFeeValues = tuple(range(0, 10000, 1000) + range(10000, 100000, 10000))


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
        self.predictions.calcScore()

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
#        try:
#            tr = TxRates.loadObject()
#        except:
#            logWrite("Unable to load txRates, calculating from scratch.")
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
            try:
                tr.saveObject()
            except:
                logWrite("SS: Error saving tr object.")

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
                    'shorterrs': shorterrs,
                    'caps': (sim.agCap, sim.exRate)
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
#        feeClasses = self.statsCache[1]['poolmfrs']
#        procrate = [r*600 for r in self.statsCache[1]['processingRate']]
#        procrateUpper = [r*600 for r in self.statsCache[1]['processingRateUpper']]
#        txByteRate = [r*600 for r in self.statsCache[1]['txByteRate']]
#        stableFeeRate = self.statsCache[1]['stableFeeRate']
#        stableStat = (stableFeeRate, txByteRate[feeClasses.index(stableFeeRate)])
#        t = threading.Thread(
#                             target=ratesGraph.updateAll,
#                             args=(feeClasses,procrate,procrateUpper,txByteRate,stableStat)
#                            )
#        t.start()
#        if not async:
#            t.join()

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


        agCap, exRate = self.statsCache[1]['caps']
        agCap = sorted(agCap, reverse=True)
        exRate = sorted(exRate, reverse=True)
        x = ['> %d' % f for f, c in agCap]
        y0 = [600*c[0] for f,c in agCap]
        y1 = [600*(c[1]-c[0]) for f,c in agCap]
        y2 = [600*c for f,c in exRate]
        t = threading.Thread(target=capsGraph.updateAll, args=(x,y0,y1,y2))
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
            # Don't need this anymore because of transient sim time limit.
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
                waitTimes, timespent, numiters = sim.transient(mapTx, stopFlag=self.getStopObject())
            except ValueError as e:
                logWrite("TransientSim error:")
                logWrite(e.message)
            else:
                logWrite("transient simul completed with time %.1f seconds and %d iters." %
                         (timespent, numiters))
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
        t = threading.Thread(target=transWaitGraph.updateAll,
                             args=(x,y,err))
        t.start()

        # Take care of case where confTime is None
        confTime = self.tStats.inverseAvgConf(1000)
        txByteRate = self.qstats['txByteRate'][0]*600
        mempoolSize = self.qstats['mempoolSize'][0]
        t = threading.Thread(target=confTimeGraph.updateAll,
                             args=(confTime,txByteRate,mempoolSize))
        t.start()


class TransientStats(object):
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
                # Consider using present time rather than the entry time
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


def getMempoolSize(mapTx, feeValues):
    mempoolSize = [
        sum([entry['size'] for entry in mapTx.values() if entry['feeRate'] >= feeValue])
        for feeValue in feeValues
    ]
    return mempoolSize
