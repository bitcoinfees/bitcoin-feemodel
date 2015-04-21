from __future__ import division

import logging
from time import time
from math import ceil

from feemodel.config import MINRELAYTXFEE
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
        self.next_update = 0
        super(TransientOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        try:
            logger.info("Starting transient online sim.")
            self.wait_for_resources()
            self.sleep_till_next()
            while not self.is_stopped():
                self.next_update = time() + self.update_period
                self.update()
                self.sleep_till_next()
        except StopIteration:
            pass
        finally:
            logger.info("Stopped transient online sim.")
            # Ensures that Prediction.update_predictions doesn't get outdated
            # values, if this thread has bugged out
            self.stats = None

    def wait_for_resources(self):
        '''Check and wait for all required resources to be ready.'''
        while not self.is_stopped() and not (
                self.txonline and self.poolsonline and self.mempool):
            self.sleep(5)

    def sleep_till_next(self):
        '''Sleep till the next update.'''
        self.sleep(max(0, self.next_update-time()))

    def update(self):
        tx_source = self.txonline.get_txsource()
        pools = self.poolsonline.get_pools()
        sim = Simul(pools, tx_source)
        feepoints = self.calc_feepoints(sim)
        init_entries = self.mempool.state.entries
        mempoolsize = sum([entry.size for entry in init_entries.values()
                           if entry.feerate >= MINRELAYTXFEE])

        feepoints, waittimes, timespent, numiters = transientsim(
            sim,
            feepoints=feepoints,
            init_entries=init_entries,
            miniters=self.miniters,
            maxiters=self.maxiters,
            maxtime=self.update_period,
            stopflag=self.get_stop_object())

        logger.info("Finished transient simulation in %.2fs and "
                    "%d iterations - mempool size was %d bytes" %
                    (timespent, numiters, mempoolsize))
        # Warn if we reached miniters
        if numiters <= self.miniters*1.1:
            logger.warning("Transient sim took %.2fs to do %d iters." %
                           (timespent, numiters))

        self.stats = TransientStats(feepoints, waittimes, timespent, numiters,
                                    mempoolsize, sim)

    def calc_feepoints(self, sim):
        """Get feepoints at which to evaluate wait times.

        The feepoints are chosen so that the wait times are approximately
        evenly spaced, 1 min apart. This is done by linear interpolation
        of previous wait times.

        If not stats have been computed yet, return None (i.e. use the
        default feepoints computed by transientsim)
        """
        d = 60  # 1 min wait between feepoints
        if not self.stats:
            return None
        waitfn = self.stats.expectedwaits
        minwait = waitfn._y[-1]
        maxwait = waitfn._y[0]
        feepoints = [
            int(waitfn.inv(wait))
            for wait in range(int(ceil(minwait)), int(ceil(maxwait))+d, d)]
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

    def __init__(self, feepoints, waittimes, timespent, numiters,
                 mempoolsize, sim):
        self.timestamp = time()
        self.timespent = timespent
        self.numiters = numiters
        self.mempoolsize = mempoolsize
        self.cap = sim.cap
        self.stablefeerate = sim.stablefeerate

        expectedwaits = []
        expectedwaits_err = []
        waitmatrix = []
        for waitsample in waittimes:
            waitdata = DataSample(waitsample)
            waitdata.calc_stats()
            expectedwaits.append(waitdata.mean)
            expectedwaits_err.append(waitdata.mean_interval[1]-waitdata.mean)
            waitmatrix.append(
                [waitdata.get_percentile(p) for p in WAIT_PERCENTILE_PTS])

        self.feepoints = feepoints
        self.expectedwaits = WaitFn(feepoints, expectedwaits,
                                    expectedwaits_err)
        self.waitmatrix = [WaitFn(feepoints, w) for w in zip(*waitmatrix)]

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
            'cap': self.cap.get_stats(),
            'stablefeerate': self.stablefeerate,
            'feepoints': self.feepoints,
            'expectedwaits': self.expectedwaits.waits,
            'expectedwaits_errors': self.expectedwaits.errors,
            'waitpercentiles': [w.waits for w in self.waitmatrix],
            'mempoolsize': self.mempoolsize
        }
        return stats

    # def update(self):
    #     self._updating = True
    #     currheight = proxy.getblockcount()
    #     blockrangetuple = (currheight-self.window+1, currheight+1)
    #     if currheight > self.tx_source.height:
    #         self.tx_source.start(blockrangetuple,
    #                              stopflag=self.get_stop_object())
    #     pools = deepcopy(self.peo.pe)
    #     assert pools
    #     pools.calc_blockrate()
    #     # TODO: catch unstable error
    #     sim = Simul(pools, self.tx_source)
    #     feeclasses = get_feeclasses(sim.cap, sim.stablefeerate)
    #     self.simulate(sim, feeclasses)
    #     self._updating = False

    # def simulate(self, sim, feeclasses):
    #     stats = TransientStats()
    #     stats.timestamp = time()
    #     init_entries = [SimEntry.from_mementry(txid, entry)
    #                     for txid, entry in
    #                     self.mempool.get_entries().items()]
    #     mempoolsize = sum([entry.tx.size for entry in init_entries
    #                        if entry.tx.feerate >= minrelaytxfee])

    #     tstats = {feerate: DataSample() for feerate in feeclasses}
    #     simtime = 0.
    #     stranded = set(feeclasses)
    #     numiters = 0
    #     for block, realtime in sim.run(init_entries=init_entries):
    #         if self.is_stopped():
    #             raise StopIteration
    #         simtime += block.interval
    #         stranding_feerate = block.sfr

    #         for feerate in list(stranded):
    #             if feerate >= stranding_feerate:
    #                 tstats[feerate].add_datapoints([simtime])
    #                 stranded.remove(feerate)

    #         if not stranded:
    #             numiters += 1
    #             if (numiters >= self.maxiters or
    #                     numiters >= self.miniters and
    #                     realtime > self.maxtime):
    #                 break
    #             else:
    #                 simtime = 0.
    #                 stranded = set(feeclasses)
    #                 sim.mempool.reset()

    #     logger.info("Finished transient simulation in %.2fs and "
    #                 "%d iterations - mempool size was %d bytes" %
    #                 (realtime, numiters, mempoolsize))
    #     # Warn if we reached miniters
    #     if numiters == self.miniters:
    #         logger.warning("Transient sim took %.2fs to do %d iters." %
    #                        (realtime, numiters))

    #     stats.tstats = tstats
    #     stats.numiters = numiters
    #     stats.timespent = realtime
    #     stats.cap = sim.cap
    #     stats.stablefeerate = sim.stablefeerate
    #     stats.mempoolsize = mempoolsize
    #     self.stats = stats
    #     # self.next_update = stats.timestamp + self.update_period

    # @property
    # def status(self):
    #     if self._updating is None:
    #         return 'stopped'
    #     elif self._updating:
    #         return 'running'
    #     else:
    #         return 'idle'

    # def _calc_windowfill(self):
    #     '''Calculate window fill ratio.

    #     Returns the ratio of the number of available memblocks within
    #     the window, to the window size.
    #     '''
    #     currheight = proxy.getblockcount()
    #     windowrange = (currheight-self.window+1, currheight+1)
    #     numblocks = len(MemBlock.get_heights(windowrange))
    #     return numblocks / self.window


# #class TransientStats(SimStats):
# #    def __init__(self, predict_level=default_predict_level):
# #        self.predict_level = predict_level
# #        self.tstats = None
# #        super(TransientStats, self).__init__()
# #
# #    def predict(self, feerate):
# #        '''Predict the wait time of a transaction with specified feerate.
# #
# #        Returns t such that the wait time of the transaction, given the
# #        current mempool state, is less than t seconds with probability
# #        self.predict_level.
# #        '''
# #        if not self:
# #            return None
# #        return self.predictwaits(feerate)
# #
# #    @property
# #    def tstats(self):
# #        return self._tstats
# #
# #    @tstats.setter
# #    def tstats(self, tstats):
# #        self._tstats = tstats
# #        if not tstats:
# #            self.avgwaits = None
# #            self.predictwaits = None
# #            return
# #        titems = sorted(tstats.items())
# #        for feerate, stat in titems:
# #            stat.calc_stats()
# #        avgwaits = [stat.mean for feerate, stat in titems]
# #        errors = [stat.mean_interval[1]-stat.mean for f, stat in titems]
# #        feerates = [feerate for feerate, stat in titems]
# #        self.avgwaits = WaitFn(feerates, avgwaits, errors)
# #
# #        predictwaits = [stat.get_percentile(self.predict_level)
# #                        for f, stat in titems]
# #        self.predictwaits = WaitFn(feerates, predictwaits)
# #
# #    def print_stats(self):
# #        super(TransientStats, self).print_stats()
# #        if self:
# #            self.avgwaits.print_fn()
# #
# #    def get_stats(self):
# #        if not self:
# #            return None
# #        basestats = super(TransientStats, self).get_stats()
# #        stats = {
# #            'feerates': self.avgwaits.feerates,
# #            'avgwaits': self.avgwaits.waits,
# #            'avgwaits_errors': self.avgwaits.errors,
# #            'predictwaits': self.predictwaits.waits,
# #            'predictlevel': self.predict_level,
# #            'mempoolsize': self.mempoolsize
# #        }
# #        basestats.update(stats)
# #        return basestats
# #
# #    def __nonzero__(self):
# #        return bool(self.tstats)

# def __init__(self, mempool, peo, window,
#              update_period=default_update_period,
#              miniters=default_miniters, maxiters=default_maxiters,
#              maxtime=default_maxtime):
#     self.stats_lock = threading.Lock()
#     self.mempool = mempool
#     self.peo = peo
#     self.window = window
#     self.update_period = update_period
#     self.miniters = miniters
#     self.maxiters = maxiters
#     self.maxtime = maxtime
#     self.tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
#     self.stats = TransientStats()
#     self.next_update = self.stats.timestamp + update_period
#     self._updating = None
#     super(TransientOnline, self).__init__()
