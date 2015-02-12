from __future__ import division

import threading
import logging
from time import time
from copy import deepcopy

from feemodel.util import StoppableThread, DataSample, proxy
from feemodel.simul import Simul, SimTx
from feemodel.simul.stats import SimStats, WaitFn, get_feeclasses
from feemodel.estimate import TxRateEstimator

tx_maxsamplesize = 10000
default_update_period = 60.
default_miniters = 1000
default_maxiters = 10000
default_maxtime = 60.
default_predict_level = 0.9

logger = logging.getLogger(__name__)


class TransientOnline(StoppableThread):
    def __init__(self, mempool, peo, window,
                 update_period=default_update_period,
                 miniters=default_miniters, maxiters=default_maxiters,
                 maxtime=default_maxtime):
        self.stats_lock = threading.Lock()
        self.mempool = mempool
        self.peo = peo
        self.window = window
        self.update_period = update_period
        self.miniters = miniters
        self.maxiters = maxiters
        self.maxtime = maxtime
        self.tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
        self.stats = TransientStats()
        self.next_update = self.stats.timestamp + update_period
        self._updating = None
        super(TransientOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        try:
            self._updating = False
            logger.info("Starting transient online sim.")
            self.sleep(max(0, self.next_update-time()))
            while not self.is_stopped() and not (
                    self.peo.pe and self.mempool):
                self.sleep(10)
            while not self.is_stopped():
                self.update()
                self.sleep(max(0, self.next_update-time()))
        except StopIteration:
            pass
        finally:
            logger.info("Stopped transient online sim.")
            # Ensure that Prediction.update_predictions doesn't get outdated
            # values, if this thread has bugged out
            self.stats = TransientStats()
            self._updating = None

    def update(self):
        self._updating = True
        currheight = proxy.getblockcount()
        blockrangetuple = (currheight-self.window+1, currheight+1)
        if currheight > self.tx_source.height:
            self.tx_source.start(blockrangetuple,
                                 stopflag=self.get_stop_object())
        pools = deepcopy(self.peo.pe)
        assert pools
        pools.calc_blockrate()
        # TODO: catch unstable error
        sim = Simul(pools, self.tx_source)
        feeclasses = get_feeclasses(sim.cap, self.tx_source, sim.stablefeerate)
        self.simulate(sim, feeclasses)
        self._updating = False

    def simulate(self, sim, feeclasses):
        stats = TransientStats()
        stats.timestamp = time()
        mempooltxs = [SimTx.from_mementry(txid, entry)
                      for txid, entry in self.mempool.get_entries().items()]
        mempoolsize = sum([tx.size for tx in mempooltxs
                           if tx.feerate >= sim.stablefeerate])

        tstats = {feerate: DataSample() for feerate in feeclasses}
        simtime = 0.
        stranded = set(feeclasses)
        numiters = 0
        for block, realtime in sim.run(mempooltxs=mempooltxs):
            if self.is_stopped():
                raise StopIteration
            simtime += block.interval
            stranding_feerate = block.sfr

            for feerate in list(stranded):
                if feerate >= stranding_feerate:
                    tstats[feerate].add_datapoints([simtime])
                    stranded.remove(feerate)

            if not stranded:
                numiters += 1
                if (numiters >= self.maxiters or
                        numiters >= self.miniters and realtime > self.maxtime):
                    break
                else:
                    simtime = 0.
                    stranded = set(feeclasses)
                    sim.mempool.reset()

        logger.info("Finished transient simulation in %.2fs and "
                    "%d iterations - mempool size was %d bytes" %
                    (realtime, numiters, mempoolsize))
        # Warn if we reached miniters
        if numiters == self.miniters:
            logger.warning("Transient sim took %.2fs to do %d iters." %
                           (realtime, numiters))

        stats.tstats = tstats
        stats.numiters = numiters
        stats.timespent = realtime
        stats.cap = sim.cap
        stats.stablefeerate = sim.stablefeerate
        stats.mempoolsize = mempoolsize
        self.stats = stats
        self.next_update = stats.timestamp + self.update_period

    @property
    def stats(self):
        with self.stats_lock:
            return self._stats

    @stats.setter
    def stats(self, val):
        with self.stats_lock:
            self._stats = val

    @property
    def status(self):
        if self._updating is None:
            return 'stopped'
        elif self._updating:
            return 'running'
        else:
            return 'idle'


class TransientStats(SimStats):
    def __init__(self, predict_level=default_predict_level):
        self.predict_level = predict_level
        self.tstats = None
        super(TransientStats, self).__init__()

    def predict(self, feerate):
        '''Predict the wait time of a transaction with specified feerate.

        Returns t such that the wait time of the transaction, given the
        current mempool state, is less than t seconds with probability
        self.predict_level.
        '''
        if not self:
            return None
        return self.predictwaits(feerate)

    @property
    def tstats(self):
        return self._tstats

    @tstats.setter
    def tstats(self, tstats):
        self._tstats = tstats
        if not tstats:
            self.avgwaits = None
            self.predictwaits = None
            return
        titems = sorted(tstats.items())
        for feerate, stat in titems:
            stat.calc_stats()
        avgwaits = [stat.mean for feerate, stat in titems]
        errors = [stat.mean_interval[1]-stat.mean for f, stat in titems]
        feerates = [feerate for feerate, stat in titems]
        self.avgwaits = WaitFn(feerates, avgwaits, errors)

        predictwaits = [stat.get_percentile(self.predict_level)
                        for f, stat in titems]
        self.predictwaits = WaitFn(feerates, predictwaits)

    def print_stats(self):
        super(TransientStats, self).print_stats()
        if self:
            self.avgwaits.print_fn()

    def get_stats(self):
        if not self:
            return None
        basestats = super(TransientStats, self).get_stats()
        stats = {
            'feerates': self.avgwaits.feerates,
            'avgwaits': self.avgwaits.waits,
            'avgwaits_errors': self.avgwaits.errors,
            'predictwaits': self.predictwaits.waits,
            'predictlevel': self.predict_level
        }
        basestats.update(stats)
        return stats

    def __nonzero__(self):
        return bool(self.tstats)
