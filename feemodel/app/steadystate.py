import logging
import threading
import os
from copy import deepcopy

from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, proxy
from feemodel.util import StoppableThread, DataSample
from feemodel.estimate.txrate import TxRateEstimator
from feemodel.simul import Simul
from feemodel.simul.simul import get_feeclasses
from feemodel.waitmeasure import WaitMeasure
from feemodel.queuestats import QueueStats

logger = logging.getLogger(__name__)

tx_maxsamplesize = 100000
default_update_period = 144
default_maxiters = 100000
default_maxtime = 600


class SteadyStateOnline(StoppableThread):

    savedir = os.path.join(datadir, 'steadystate')

    def __init__(self, peo, window, update_period=default_update_period,
                 maxiters=default_maxiters, maxtime=default_maxtime):
        self.stats_lock = threading.Lock()
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

        pools = self.peo.pe
        if not pools.get_numpools():
            logger.debug("No pools.")
            return
        sim = Simul(pools, tx_source)
        feeclasses = get_feeclasses(sim.cap, tx_source, sim.stablefeerate)
        stats = self.simulate(sim, feeclasses)

        if feeclasses != stats.waitmeasure.feerates:
            stats.waitmeasure = WaitMeasure(feeclasses)
        stats.waitmeasure.calcwaits(blockrangetuple,
                                    stopflag=self.get_stop_object())
        stats.height = currheight
        self.stats = stats

        try:
            self.save_stats()
        except:
            logger.exception("Unable to save steady-state stats.")

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

        stats = deepcopy(self.stats)
        stats.qstats = qstats
        stats.shortstats = shortstats
        stats.cap = sim.cap
        stats.stablefeerate = sim.stablefeerate
        return stats

    @property
    def stats(self):
        with self.stats_lock:
            return self._stats

    @stats.setter
    def stats(self, val):
        with self.stats_lock:
            self._stats = val

    def load_stats(self):
        savefiles = sorted(os.listdir(self.savedir))
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.stats = load_obj(savefile)
        # Put in the loaded info

    def save_stats(self):
        savefilename = 'ss' + str(self.stats.height) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.stats, savefile)

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
