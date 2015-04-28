from __future__ import division

import logging
from time import time

from feemodel.util import StoppableThread, DataSample
from feemodel.simul import Simul
from feemodel.simul.stats import WaitFn
from feemodel.simul.transient import transientsim
from feemodel.app.predict import WAIT_PERCENTILE_PTS, TxPrediction

default_update_period = 60.
default_miniters = 2000
default_maxiters = 10000

logger = logging.getLogger(__name__)


class TransientOnline(StoppableThread):

    def __init__(self, mempool, poolsonline, txonline,
                 update_period=default_update_period,
                 miniters=default_miniters,
                 maxiters=default_maxiters):
        self.mempool = mempool
        self.txonline = txonline
        self.poolsonline = poolsonline
        self.update_period = update_period
        self.miniters = miniters
        self.maxiters = maxiters

        self.stats = None
        super(TransientOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        logger.info("Starting transient online sim.")
        while not self.is_stopped():
            try:
                self.update()
            except StopIteration:
                pass
            self.sleep_till_next()
        logger.info("Stopped transient online sim.")
        # Ensures that Prediction.update_predictions doesn't get outdated
        # values, if this thread has bugged out
        self.stats = None

    def sleep_till_next(self):
        '''Sleep till the next update.'''
        stats = self.stats
        if stats is not None:
            time_till_next = max(
                stats.timestamp + self.update_period - time(), 0)
            self.sleep(time_till_next)

    def update(self):
        sim, init_entries = self._get_resources()
        feepoints = self.calc_feepoints(sim)

        stats = TransientStats()
        feepoints, waittimes = transientsim(
            sim,
            feepoints=feepoints,
            init_entries=init_entries,
            miniters=self.miniters,
            maxiters=self.maxiters,
            maxtime=self.update_period,
            stopflag=self.get_stop_object())
        stats.record_waittimes(feepoints, waittimes)

        logger.debug("Finished transient sim in %.2fs and %d iterations" %
                     (stats.timespent, stats.numiters))
        # Warn if we reached miniters
        if stats.timespent > 1.1*self.update_period:
            logger.warning("Transient sim took %.2fs to do %d iters." %
                           (stats.timespent, stats.numiters))
        self.stats = stats

    def _get_resources(self):
        """Get transient sim resources.

        Get the Simul instance (which requires SimPools and SimTxSource)
        and mempool entries. If any are not ready, retry every 5 seconds.
        """
        while not self.is_stopped():
            pools = self.poolsonline.get_pools()
            tx_source = self.txonline.get_txsource()
            state = self.mempool.state
            if state and pools and tx_source:
                return Simul(pools, tx_source), state.entries
            self.sleep(5)
        raise StopIteration

    def calc_feepoints(self, sim, max_wait_delta=60, min_num_pts=20):
        """Get feepoints at which to evaluate wait times.

        The feepoints are chosen so that the wait times are approximately
        evenly spaced, 1 min apart. This is done by linear interpolation
        of previous wait times.

        If not stats have been computed yet, return None (i.e. use the
        default feepoints computed by transientsim)
        """
        if not self.stats:
            return None
        waitfn = self.stats.expectedwaits
        minwait = waitfn._y[-1]
        maxwait = waitfn._y[0]
        wait_delta = min(max_wait_delta,
                         (maxwait - minwait) / (min_num_pts - 1))
        wait_delta = max(wait_delta, 1)
        num_pts = 1 + int(round((maxwait - minwait) / wait_delta))
        wait_pts = [minwait + wait_delta*i for i in range(num_pts)]
        feepoints = [int(round(waitfn.inv(wait))) for wait in wait_pts]

        minfeepoint = sim.stablefeerate
        maxfeepoint = sim.cap.feerates[sim.cap.cap_ratio_index(0.05)]
        for idx, cap in enumerate(sim.cap.caps):
            if cap >= 0.95*sim.cap.caps[-1]:
                alt_maxfeepoint = sim.cap.feerates[idx]
                break
        maxfeepoint = max(maxfeepoint, alt_maxfeepoint)
        feepoints.extend([minfeepoint, maxfeepoint])
        feepoints = filter(
            lambda feerate: minfeepoint <= feerate <= maxfeepoint,
            sorted(set(feepoints)))
        return feepoints

    def get_stats(self):
        stats = {
            'params': {
                'miniters': self.miniters,
                'maxiters': self.maxiters,
                'update_period': self.update_period
            }
        }
        tstats = self.stats
        if tstats is not None:
            stats.update(tstats.get_stats())
        return stats


class TransientStats(object):

    def __init__(self):
        self.timestamp = time()

    def record_waittimes(self, feepoints, waittimes):
        self.timespent = time() - self.timestamp
        self.numiters = len(waittimes[0])

        expectedwaits = []
        expectedwaits_err = []
        waitpercentiles = []
        for waitsample in waittimes:
            waitdata = DataSample(waitsample)
            waitdata.calc_stats()
            expectedwaits.append(waitdata.mean)
            expectedwaits_err.append(waitdata.mean_interval[1]-waitdata.mean)
            waitpercentiles.append(
                [waitdata.get_percentile(p) for p in WAIT_PERCENTILE_PTS])

        self.feepoints = feepoints
        self.expectedwaits = WaitFn(feepoints, expectedwaits,
                                    expectedwaits_err)
        self.waitmatrix = [WaitFn(feepoints, w) for w in zip(*waitpercentiles)]

    def predict(self, feerate, currtime):
        '''Predict the wait time of a transaction with specified feerate.

        entry is a mementry object. Returns a TxPrediction object.
        '''
        if feerate < self.feepoints[0]:
            return None
        waitpercentiles = [w(feerate) for w in self.waitmatrix]
        return TxPrediction(waitpercentiles, feerate, currtime)

    def get_stats(self):
        stats = {
            'timestamp': self.timestamp,
            'timespent': self.timespent,
            'numiters': self.numiters,
            'feepoints': self.feepoints,
            'expectedwaits': self.expectedwaits.waits,
            'expectedwaits_errors': self.expectedwaits.errors,
            'waitmatrix': [w.waits for w in self.waitmatrix],
        }
        return stats
