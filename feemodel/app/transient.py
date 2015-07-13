from __future__ import division

import logging
from time import time
from math import ceil, sqrt
from collections import defaultdict

from feemodel.util import StoppableThread, DataSample
from feemodel.simul import Simul
from feemodel.simul.stats import WaitFn
from feemodel.simul.transient import transientsim
from feemodel.app.predict import WAIT_PERCENTILE_PTS, TxPrediction
from feemodel.config import EXPECTED_BLOCK_INTERVAL

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
        pools, tx_source, mempoolstate = self._get_resources()
        sim = Simul(pools, tx_source)
        feepoints = self.calc_feepoints(sim, mempoolstate)
        init_entries = remove_lowfee(mempoolstate.entries, sim.stablefeerate)

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

        Get the SimPools, SimTxSource, and MempoolState objects.  If any are
        not ready, retry every 5 seconds.
        """
        while not self.is_stopped():
            pools = self.poolsonline.get_pools()
            tx_source = self.txonline.get_txsource()
            mempoolstate = self.mempool.state
            if mempoolstate and pools and tx_source:
                return pools, tx_source, mempoolstate
            # Resources aren't available due to some error elsewhere,
            # so get rid of stats to avoid giving stale stats to others.
            self.stats = None
            self.sleep(5)
        raise StopIteration

    def calc_feepoints(self, sim, mempoolstate,
                       max_wait_delta=60, min_num_pts=20):
        """Get feepoints at which to evaluate wait times.

        The feepoints are chosen so that the wait times are approximately
        evenly spaced, 1 min apart. This is done by linear interpolation
        of previous wait times.

        If not stats have been computed yet, return None (i.e. use the
        default feepoints computed by transientsim)
        """
        mempool_sizefn = mempoolstate.get_sizefn()
        maxcap = sim.cap.capfn[-1][1]
        minfeepoint = None
        for feerate, txbyterate in sim.cap.txbyteratefn:
            if feerate < sim.stablefeerate:
                continue
            capdelta = maxcap - txbyterate
            assert capdelta > 0
            mempoolsize = mempool_sizefn(feerate)
            if mempoolsize / capdelta < 10800:
                # Roughly 3 hours to clear
                minfeepoint = feerate
                break
        if minfeepoint is None:
            minfeepoint = feerate
        # No need to process transactions with fee rate lower than minfeepoint
        sim.stablefeerate = max(sim.stablefeerate, minfeepoint)

        if not self.stats:
            # Use default feepoints - even spacing
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

        maxfeepoint = sim.cap.inv_util(0.05)
        # maxfeepoint must also be at least the 0.95 cap feerate
        for feerate, cap in sim.cap.capfn:
            if cap >= 0.95*maxcap:
                alt_maxfeepoint = feerate
                break
        # maxfeepoint must also be at least so that mempoolsize is "small"
        alt_maxfeepoint2 = int(
            mempool_sizefn.inv(0.1*maxcap*EXPECTED_BLOCK_INTERVAL))
        maxfeepoint = max(maxfeepoint, alt_maxfeepoint, alt_maxfeepoint2)

        minfeepoint = sim.stablefeerate

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
            expectedwaits_err.append(waitdata.std / sqrt(self.numiters))
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

    def estimatefee(self, waitminutes):
        feerate = self.expectedwaits.inv(waitminutes*60)
        if feerate is not None:
            feerate = int(ceil(feerate))
        return feerate

    def decidefee(self, txsize, ten_minute_cost, waitcostfn="quadratic"):
        """Compute the optimal transaction fee.

        The cost of a transaction is modeled as:

        C = txfee + f(waittime)

        where f, the wait cost function, is non-decreasing and f(0) = 0.

        This method thus computes the optimal fee (not feerate) in satoshis,
        with respect to expected cost, for a transaction of size <txsize>
        and a given wait cost function f. We restrict f by the following
        two parameters:

        1. <ten_minute_cost>: the cost in satoshis of a wait time of 10 min.
        2. <waitcostfn> in ('linear', 'quadratic'): specify whether f is
           linear or quadratic in the wait time.

        In the future perhaps this method could be generalized to accept
        arbitrary wait cost functions.
        """
        if waitcostfn == "linear":
            waitcosts = [meanwait / 600 * ten_minute_cost
                         for meanwait in self.expectedwaits.waits]
        elif waitcostfn == "quadratic":
            # mean squared wait = var(wait) + mean(wait)^2
            meansq_waits = [
                self.numiters*stderr**2 + meanwait**2
                for stderr, meanwait in
                zip(self.expectedwaits.errors, self.expectedwaits.waits)]
            waitcosts = [meansq_wait / 360000 * ten_minute_cost
                         for meansq_wait in meansq_waits]
        else:
            raise ValueError("waitcostfn keyword arg must be "
                             "'linear' or 'quadratic'.")

        C_array = [feerate*txsize/1000 + waitcost
                   for feerate, waitcost in zip(self.feepoints, waitcosts)]

        bestidx = min(enumerate(C_array), key=lambda c: c[1])[0]
        C = C_array[bestidx]
        best_feerate = self.feepoints[bestidx]
        best_fee = int(ceil(best_feerate * txsize / 1000))
        expectedwait = self.expectedwaits.waits[bestidx]
        return best_fee, expectedwait, C

    def get_stats(self):
        stats = {
            'timestamp': self.timestamp,
            'timespent': self.timespent,
            'numiters': self.numiters,
            'feepoints': self.feepoints,
            'expectedwaits': self.expectedwaits.waits,
            'expectedwaits_stderr': self.expectedwaits.errors,
            'waitmatrix': [w.waits for w in self.waitmatrix],
        }
        return stats


def remove_lowfee(entries, feethresh):
    """Remove all low fee (< feethresh) transactions and their dependants.
    """
    # Build a dependency map
    depmap = defaultdict(list)
    for txid, entry in entries.items():
        for dep in entry.depends:
            depmap[dep].append(txid)
    removed = set()
    for txid, entry in entries.items():
        if entry.feerate < feethresh:
            removelist = [txid]
            while removelist:
                txid_remove = removelist.pop()
                if txid_remove in removed:
                    continue
                removed.add(txid_remove)
                removelist.extend(depmap[txid_remove])
    return {txid: entry for txid, entry in entries.items()
            if txid not in removed}
