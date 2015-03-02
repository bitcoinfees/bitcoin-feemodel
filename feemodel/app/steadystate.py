from __future__ import division

import logging
import threading
import os
from copy import deepcopy
from time import time

from feemodel.config import datadir, windowfillthresh
from feemodel.util import save_obj, load_obj, proxy
from feemodel.util import StoppableThread, DataSample
from feemodel.estimate.txrate import TxRateEstimator
from feemodel.simul import Simul
from feemodel.simul.stats import SimStats, get_feeclasses, WaitFn
from feemodel.waitmeasure import WaitMeasure
from feemodel.queuestats import QueueStats
from feemodel.txmempool import MemBlock

logger = logging.getLogger(__name__)

# TODO: move maxsamplesize to config
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
        except Exception:
            logger.info("Unable to load saved stats.")
            self.stats = SteadyStateStats()
        else:
            if time() - self.stats.timestamp > self.update_period:
                logger.info("Loaded stats are outdated; "
                            "starting from scratch.")
                self.stats = SteadyStateStats()
            else:
                logger.info("Steady-state stats loaded.")

        self.next_update = self.stats.timestamp + update_period
        self._updating = None
        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        super(SteadyStateOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        try:
            logger.info("Starting steady-state online sim.")
            logger.info("Windowfill is %.2f." % self._calc_windowfill())
            self.wait_for_resources()
            self._updating = False
            self.sleep_till_next()
            while not self.is_stopped():
                self.update()
                self.sleep_till_next()
        except StopIteration:
            pass
        finally:
            logger.info("Stopped steady-state online sim.")
            self._updating = None

    def wait_for_resources(self):
        '''Check and wait for all required resources to be ready.'''
        while not self.is_stopped() and not (
                self.peo.pe and
                self._calc_windowfill() >= windowfillthresh):
            self.sleep(10)

    def sleep_till_next(self):
        '''Sleep till the next update.'''
        self.sleep(max(0, self.next_update-time()))

    def update(self):
        self._updating = True
        waitmeasure = deepcopy(self.stats.waitmeasure)
        starttime = time()

        currheight = proxy.getblockcount()
        blockrangetuple = (currheight-self.window+1, currheight+1)
        tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
        tx_source.start(blockrangetuple, stopflag=self.get_stop_object())

        pools = deepcopy(self.peo.pe)
        assert pools
        pools.calc_blockrate()

        # TODO: catch unstable error
        sim = Simul(pools, tx_source)
        feeclasses = get_feeclasses(sim.cap, tx_source, sim.stablefeerate)
        stats = self.simulate(sim, feeclasses)

        if feeclasses != waitmeasure.feerates:
            waitmeasure = WaitMeasure(feeclasses)
        waitmeasure.calcwaits(blockrangetuple, stopflag=self.get_stop_object())
        stats.waitmeasure = waitmeasure
        stats.timestamp = starttime

        self.stats = stats
        self.next_update = starttime + self.update_period
        try:
            self.save_stats(currheight)
        except Exception:
            logger.exception("Unable to save steady-state stats.")
        self._updating = False

    def simulate(self, sim, feeclasses):
        qstats = QueueStats(feeclasses)
        qshortstats = QueueStats(feeclasses)
        shortstats = {feerate: DataSample() for feerate in feeclasses}

        logger.info("Beginning steady-state simulation..")
        for block, realtime in sim.run():
            if self.is_stopped():
                raise StopIteration
            if block.height >= self.maxiters or (
                    block.height >= self.miniters and
                    realtime > self.maxtime):
                break
            qstats.next_block(block.height, block.interval, block.sfr)
            qshortstats.next_block(block.height, block.interval, block.sfr)
            if not (block.height + 1) % self.window:
                for queueclass in qshortstats.stats:
                    shortstats[queueclass.feerate].add_datapoints(
                        [queueclass.avgwait])
                qshortstats = QueueStats(feeclasses)
        logger.info("Finished steady-state simulation in %.2fs "
                    "and %d iterations." % (realtime, block.height))
        # Warn if we reached miniters
        if block.height == self.miniters:
            logger.warning("Steadystate sim took %.2fs to do %d iters." %
                           (realtime, block.height))

        stats = SteadyStateStats()
        stats.qstats = qstats
        stats.shortstats = shortstats
        stats.timespent = realtime
        stats.numiters = block.height
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

    def save_stats(self, currheight):
        savefilename = 'ss' + str(currheight) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.stats, savefile)

    @property
    def status(self):
        if self._updating is None:
            return 'stopped'
        elif self._updating:
            return 'running'
        else:
            return 'idle'

    def _calc_windowfill(self):
        '''Calculate window fill ratio.

        Returns the ratio of the number of available memblocks within
        the window, to the window size.
        '''
        currheight = proxy.getblockcount()
        windowrange = (currheight-self.window+1, currheight+1)
        numblocks = len(MemBlock.get_heights(windowrange))
        return numblocks / self.window


class SteadyStateStats(SimStats):
    def __init__(self):
        self.qstats = None
        self.shortstats = None
        self.waitmeasure = WaitMeasure([])
        super(SteadyStateStats, self).__init__()

    def print_stats(self):
        if not self:
            return
        super(SteadyStateStats, self).print_stats()
        # TODO: modify to print more details
        self.avgwaits.print_fn()
        self.m_avgwaits.print_fn()

    @property
    def qstats(self):
        return self._qstats

    @qstats.setter
    def qstats(self, qstats):
        self._qstats = qstats
        if qstats:
            feerates = [stat.feerate for stat in qstats.stats]
            avgwaits = [stat.avgwait for stat in qstats.stats]
            self.avgwaits = WaitFn(feerates, avgwaits)
            self.strandedprop = [stat.stranded_proportion
                                 for stat in qstats.stats]
            self.avg_strandedblocks = [stat.avg_strandedblocks
                                       for stat in qstats.stats]
        else:
            self.avgwaits = None
            self.strandedprop = None
            self.avg_strandedblocks = None

    @property
    def shortstats(self):
        return self._shortstats

    @shortstats.setter
    def shortstats(self, shortstats):
        self._shortstats = shortstats
        if shortstats:
            sitems = sorted(shortstats.items())

            self.m_errors = []
            for feerate, stat in sitems:
                try:
                    stat.calc_stats()
                except ValueError:
                    # This shouldn't happen.
                    self.m_errors.append(float("inf"))
                else:
                    self.m_errors.append(1.96*stat.std)
        else:
            self.m_errors = None

    @property
    def waitmeasure(self):
        return self._waitmeasure

    @waitmeasure.setter
    def waitmeasure(self, waitmeasure):
        self._waitmeasure = waitmeasure
        if not waitmeasure:
            self.m_avgwaits = None
            return
        if not self.m_errors:
            raise ValueError("shortstats needs to be set first.")
        avgwaits = zip(waitmeasure.feerates, waitmeasure.waitstat.avgwaits,
                       self.m_errors, waitmeasure.waitstat.numtxs)
        avgwaits = filter(lambda a: a[3] > 0, avgwaits)
        feerates, waits, errors, self.m_numtxs = zip(*avgwaits)
        feerates_binctr = [
            (feerates[idx] + feerates[idx-1]) / 2
            for idx in range(1, len(feerates))]
        self.m_avgwaits = WaitFn(feerates_binctr, waits[:-1], errors[:-1])

    def get_stats(self):
        if not self:
            return None
        basestats = super(SteadyStateStats, self).get_stats()
        stats = {
            'sim': {
                'feerates': self.avgwaits.feerates,
                'avgwaits': self.avgwaits.waits,
                'strandedprop': self.strandedprop,
                'avg_strandedblocks': self.avg_strandedblocks},
            'measured': {
                'feerates': self.m_avgwaits.feerates,
                'avgwaits': self.m_avgwaits.waits,
                'errors': self.m_avgwaits.errors,
                'numtxs': self.m_numtxs}
        }
        basestats.update(stats)
        return basestats
