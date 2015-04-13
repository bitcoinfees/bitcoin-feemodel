from __future__ import division

import logging
from time import time
from bisect import bisect

from feemodel.config import minrelaytxfee
from feemodel.util import StoppableThread, DataSample
from feemodel.simul import Simul
from feemodel.simul.stats import WaitFn
from feemodel.simul.transient import transientsim
from feemodel.app.predict import WAIT_PERCENTILE_PTS, TxPrediction

default_update_period = 60.
default_miniters = 1000
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
        self._updating = None
        super(TransientOnline, self).__init__()

    @StoppableThread.auto_restart(60)
    def run(self):
        try:
            logger.info("Starting transient online sim.")
            self.wait_for_resources()
            self._updating = False
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
            self._updating = None

    def wait_for_resources(self):
        '''Check and wait for all required resources to be ready.'''
        while not self.is_stopped() and not (
                self.txonline and self.poolsonline and self.mempool):
            self.sleep(5)

    def sleep_till_next(self):
        '''Sleep till the next update.'''
        self.sleep(max(0, self.next_update-time()))

    def update(self):
        self._updating = True
        tx_source = self.txonline.get_txsource()
        pools = self.poolsonline.get_pools()
        # TODO: catch unstable error
        sim = Simul(pools, tx_source)
        init_entries = self.mempool.state.get_entries()
        waittimes, timespent, numiters = transientsim(
            sim,
            init_entries=init_entries,
            miniters=self.miniters,
            maxiters=self.maxiters,
            maxtime=self.update_period,
            stopflag=self.get_stop_object())
        mempoolsizes, mempoolsize_with_fee = self._calc_mempoolsizes(
            init_entries, sorted(waittimes.keys()))

        logger.info("Finished transient simulation in %.2fs and "
                    "%d iterations - mempool size was %d bytes" %
                    (timespent, numiters, mempoolsize_with_fee))
        # Warn if we reached miniters
        if numiters == self.miniters:
            logger.warning("Transient sim took %.2fs to do %d iters." %
                           (timespent, numiters))

        self.stats = TransientStats(waittimes, timespent, numiters, sim,
                                    mempoolsizes, mempoolsize_with_fee)
        self._updating = False

    @staticmethod
    def _calc_mempoolsizes(entries, feerates):
        '''Calculate the reverse cumulative (wrt feerate) mempool size.

        feerates is assumed sorted.
        '''
        mempoolsize_with_fee = 0
        sizebins = [0]*len(feerates)
        for entry in entries.values():
            fidx = bisect(feerates, entry.feerate)
            if fidx:
                sizebins[fidx-1] += entry.size
            if entry.feerate >= minrelaytxfee:
                mempoolsize_with_fee += entry.size
        mempoolsizes = [sum(sizebins[idx:]) for idx in range(len(sizebins))]
        return mempoolsizes, mempoolsize_with_fee


class TransientStats(object):
    def __init__(self, waittimes, timespent, numiters, sim,
                 mempoolsize, mempoolsize_with_fee):
        self.timestamp = time()
        self.timespent = timespent
        self.numiters = numiters
        self.cap = sim.cap
        self.stablefeerate = sim.stablefeerate
        self.mempoolsize = mempoolsize
        self.mempoolsize_with_fee = mempoolsize_with_fee

        feerates = []
        expectedwaits = []
        expectedwaits_err = []
        waitpercentiles = []
        for feerate, waitsample in sorted(waittimes.items()):
            waitdata = DataSample(waitsample)
            waitdata.calc_stats()
            feerates.append(feerate)
            expectedwaits.append(waitdata.mean)
            expectedwaits_err.append(waitdata.mean_interval[1]-waitdata.mean)
            waitpercentiles.append(
                [waitdata.get_percentile(p) for p in WAIT_PERCENTILE_PTS])

        self.feerates = feerates
        self.expectedwaits = WaitFn(feerates, expectedwaits,
                                    expectedwaits_err)
        self.waitpercentiles = [
            WaitFn(feerates, w)
            for w in zip(*waitpercentiles)]

    def predict(self, feerate, currtime):
        '''Predict the wait time of a transaction with specified feerate.

        entry is a mementry object. Returns a TxPrediction object.
        '''
        if feerate < self.feerates[0]:
            return None
        waitpercentiles = [w(feerate) for w in self.waitpercentiles]
        return TxPrediction(waitpercentiles, feerate, currtime)

    def get_stats(self):
        stats = {
            'timestamp': self.timestamp,
            'timespent': self.timespent,
            'numiters': self.numiters,
            'cap': self.cap.__dict__,
            'stablefeerate': self.stablefeerate,
            'feerates': self.feerates,
            'expectedwaits': self.expectedwaits.waits,
            'expectedwaits_errors': self.expectedwaits.errors,
            'waitpercentiles': [w.waits for w in self.waitpercentiles],
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
