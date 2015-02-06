import logging
import threading
import os
from copy import deepcopy
from time import time

from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, proxy
from feemodel.util import StoppableThread, DataSample
from feemodel.estimate.txrate import TxRateEstimator
from feemodel.simul import Simul
from feemodel.simul.simul import get_feeclasses
from feemodel.simul.stats import SimStats
from feemodel.waitmeasure import WaitMeasure
from feemodel.queuestats import QueueStats

logger = logging.getLogger(__name__)

tx_maxsamplesize = 100000
default_update_period = 86400
default_miniters = 100000
default_maxiters = float("inf")
default_maxtime = 600


class SteadyStateOnline(StoppableThread):

    savedir = os.path.join(datadir, 'steadystate')

    def __init__(self, peo, window, update_period=default_update_period,
                 miniters=default_miniters, maxiters=default_maxiters,
                 maxtime=default_maxtime):
        self.stats_lock = threading.Lock()
        self.peo = peo
        self.window = window
        self.update_period = update_period
        self.miniters = miniters
        self.maxiters = maxiters
        self.maxtime = maxtime
        try:
            self.load_stats()
            assert self.stats
        except:
            logger.warning("Unable to load saved stats.")
            self.stats = SteadyStateStats()
        else:
            if time() - self.stats.timestamp > self.update_period:
                logger.info("Loaded stats are outdated; "
                            "starting from scratch.")
                self.stats = SteadyStateStats()
            else:
                logger.info("Steady-state stats loaded.")

        self.next_update = self.stats.timestamp + update_period
        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        super(self.__class__, self).__init__()

    def run(self):
        logger.info("Starting steady-state online sim.")
        self.sleep(max(0, self.next_update-time()))
        while not self.peo.pe:
            self.sleep(10)
        try:
            while not self.is_stopped():
                self.update()
                self.sleep(max(0, self.next_update-time()))
        except StopIteration:
            pass
        logger.info("Stopped steady-state online sim.")

    def update(self):
        stats = deepcopy(self.stats)
        stats.timestamp = time()

        currheight = proxy.getblockcount()
        blockrangetuple = (currheight-self.window+1, currheight+1)
        tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
        tx_source.start(blockrangetuple, stopflag=self.get_stop_object())

        pools = deepcopy(self.peo.pe)
        if not pools:
            logger.debug("No pools.")
            return
        pools.calc_blockrate()

        # to-do: catch unstable error
        sim = Simul(pools, tx_source)
        feeclasses = get_feeclasses(sim.cap, tx_source, sim.stablefeerate)
        self.simulate(sim, feeclasses, stats)

        if feeclasses != stats.waitmeasure.feerates:
            stats.waitmeasure = WaitMeasure(feeclasses)
        stats.waitmeasure.calcwaits(blockrangetuple,
                                    stopflag=self.get_stop_object())

        self.stats = stats
        self.next_update = stats.timestamp + self.update_period
        try:
            self.save_stats(currheight)
        except:
            logger.exception("Unable to save steady-state stats.")

    def simulate(self, sim, feeclasses, stats):
        qstats = QueueStats(feeclasses)
        qshortstats = QueueStats(feeclasses)
        shortstats = {feerate: DataSample() for feerate in feeclasses}

        logger.info("Beginning steady-state simulation..")
        for block, realtime in sim.run(miniters=self.miniters,
                                       maxiters=self.maxiters,
                                       maxtime=self.maxtime):
            if self.is_stopped():
                raise StopIteration
            qstats.next_block(block.height, block.interval, block.sfr)
            qshortstats.next_block(block.height, block.interval, block.sfr)
            if not (block.height + 1) % self.window:
                for queueclass in qshortstats.stats:
                    shortstats[queueclass.feerate].add_datapoints(
                        [queueclass.avgwait])
                qshortstats = QueueStats(feeclasses)
        logger.info("Finished steady-state simulation in %.2fs "
                    "and %d iterations." % (realtime, block.height+1))

        stats.qstats = qstats
        stats.shortstats = shortstats
        stats.timespent = realtime
        stats.numiters = block.height + 1
        stats.cap = sim.cap
        stats.stablefeerate = sim.stablefeerate

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

    def save_stats(self, currheight):
        savefilename = 'ss' + str(currheight) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.stats, savefile)


class SteadyStateStats(SimStats):
    def __init__(self):
        self.qstats = None
        self.shortstats = None
        self.waitmeasure = WaitMeasure([])
        super(self.__class__, self).__init__()

    def print_stats(self):
        super(self.__class__, self).print_stats()
        if self.qstats:
            self.qstats.print_stats()
