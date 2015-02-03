import logging
import threading
import os
from math import ceil
from copy import deepcopy

from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, proxy
from feemodel.util import StoppableThread, DataSample
from feemodel.estimate.txrate import TxRateEstimator
from feemodel.simul import Simul
from feemodel.waitmeasure import WaitMeasure
from feemodel.queuestats import QueueStats

logger = logging.getLogger(__name__)

tx_maxsamplesize = 100000
default_maxiters = 100000
default_maxtime = 600


class SteadyStateOnline(StoppableThread):

    savedir = os.path.join(datadir, 'steadystate')

    def __init__(self, peo, window, update_period=144,
                 maxiters=default_maxiters, maxtime=default_maxtime):
        try:
            self.load_stats()
        except:
            logger.warning("Unable to load saved stats.")
            self.stats = SteadyStateStats()
        else:
            logger.info("Steady-state stats loaded with best height %d" %
                        self.stats.height)
        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        self.peo = peo
        self.window = window
        self.update_period = update_period
        self.maxiters = maxiters
        self.maxtime = maxtime
        self.stats_lock = threading.Lock()
        self._copy_cache()
        super(self.__class__, self).__init__()

    def run(self):
        logger.info("Starting steady-state online sim.")
        try:
            while not self.is_stopped():
                self.update()
                self.sleep(600)
        except StopIteration:
            pass
        logger.info("Stopped steady-state online sim.")

    def update(self):
        currheight = proxy.getblockcount()
        if currheight - self.stats.height < self.update_period:
            return
        blockrangetuple = (currheight-self.window+1, currheight+1)
        tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
        tx_source.start(blockrangetuple, stopflag=self.get_stop_object())

        pools = self.peo.get_pools()
        if not pools.get_numpools():
            logger.info("No pools.")
            return
        sim = Simul(pools, tx_source)
        feeclasses = _get_feeclasses(sim.cap, tx_source)
        self.simulate(sim, feeclasses)

        if feeclasses != self.stats.waitmeasure.feerates:
            self.stats.waitmeasure = WaitMeasure(feeclasses)
        self.stats.waitmeasure.calcwaits(blockrangetuple,
                                         stopflag=self.get_stop_object())
        self.stats.height = currheight

        try:
            self.save_stats()
        except:
            logger.exception("Unable to save steady-state stats.")
        self._copy_cache()

    def simulate(self, sim, feeclasses):
        qstats = QueueStats(feeclasses)
        qshortstats = QueueStats(feeclasses)
        shortstats = {feerate: DataSample() for feerate in feeclasses}

        logger.info("Beginning steady-state simulation..")
        for block, realtime in sim.run(maxiters=self.maxiters,
                                       maxtime=self.maxtime):
            qstats.next_block(block.height, block.interval, block.sfr)
            qshortstats.next_block(block.height, block.interval, block.sfr)
            if not (block.height + 1) % self.window:
                for queueclass in qshortstats.stats:
                    shortstats[queueclass.feerate].add_datapoints(
                        [queueclass.avgwait])
                qshortstats = QueueStats(feeclasses)
        logger.info("Finished steady-state simulation in %.2fs "
                    "and %d iterations." % (realtime, block.height+1))

        self.stats.qstats = qstats
        self.stats.shortstats = shortstats
        self.stats.cap = sim.cap
        self.stats.stablefeerate = sim.stablefeerate

    def load_stats(self):
        savefiles = sorted(os.listdir(self.savedir))
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.stats = load_obj(savefile)
        # Put in the loaded info

    def save_stats(self):
        savefilename = 'ss' + str(self.stats.height) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.stats, savefile)

    def get_stats(self):
        with self.stats_lock:
            return self.stats_cached

    def _copy_cache(self):
        with self.stats_lock:
            self.stats_cached = deepcopy(self.stats)


class SteadyStateStats(object):
    def __init__(self):
        self.qstats = None
        self.shortstats = None
        self.cap = None
        self.stablefeerate = None
        self.height = 0
        self.waitmeasure = WaitMeasure([])


def _get_feeclasses(cap, tx_source):
    feerates = cap.feerates[1:]
    caps = cap.caps
    capsdiff = [caps[idx] - caps[idx-1]
                for idx in range(1, len(feerates)+1)]
    feeDS = DataSample(feerates)
    feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
                  for p in range(5, 100, 5)]
    # Round up to nearest 1000 satoshis
    feeclasses = [int(ceil(feerate / 1000)*1000) for feerate in feeclasses]
    feeclasses = sorted(set(feeclasses))

    new_feeclasses = [True]
    while new_feeclasses:
        byterates = tx_source.get_byterates(feeclasses)
        # The byterate in each feeclass should not exceed 0.05 of the total
        byteratethresh = 0.05 * sum(byterates)
        new_feeclasses = []
        for idx, byterate in enumerate(byterates[:-1]):
            if byterate > byteratethresh:
                feegap = feeclasses[idx+1] - feeclasses[idx]
                if feegap > 1:
                    new_feeclasses.append(feeclasses[idx] + int(feegap/2))
        feeclasses.extend(new_feeclasses)
        feeclasses.sort()

    return feeclasses
