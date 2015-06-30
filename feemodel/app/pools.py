from __future__ import division

import logging
import threading
import os
from copy import copy

from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, logexceptions
from feemodel.estimate.pools import PoolsEstimatorNP

logger = logging.getLogger(__name__)

DEFAULT_MINBLOCKS = 432  # 3 days' worth
SAVEFILE = os.path.join(datadir, 'pools.pickle')


class PoolsOnlineEstimator(object):

    def __init__(self, currheight, stopflag, window,
                 minblocks=DEFAULT_MINBLOCKS):
        self.window = window
        self.minblocks = minblocks
        self.poolsestimate = None
        self.loadingthread = threading.Thread(target=self.load_estimates,
                                              args=(currheight, stopflag))
        self.loadingthread.start()

    def update(self, memblocks):
        poolsestimate = copy(self.poolsestimate)
        if poolsestimate is None:
            # Loading is not yet finished.
            # We just discard these blocks. Not ideal but doesn't change much.
            return
        for memblock in memblocks:
            if memblock is not None:
                poolsestimate.update(memblock, windowsize=self.window)
        self.check_minblocks(poolsestimate)
        self.poolsestimate = poolsestimate

    def get_pools(self):
        # We use a getter for the pool estimate as a reminder that the
        # reference is subject to change by the update method, so you
        # should bind it to another variable if you want to perform multiple
        # operations on the object.
        return self.poolsestimate

    def save_estimates(self):
        try:
            save_obj(self.poolsestimate, SAVEFILE)
        except Exception:
            logger.warning("Unable to save pools.")

    @logexceptions
    def load_estimates(self, currheight, stopflag):
        try:
            poolsestimate = load_obj(SAVEFILE)
        except Exception:
            logger.warning("Unable to load pool estimates; "
                           "starting from scratch.")
            poolsestimate = PoolsEstimatorNP()
        startheight = currheight - self.window + 1
        blockrangetuple = (startheight, currheight+1)
        poolsestimate.start(blockrangetuple, stopflag=stopflag)
        self.check_minblocks(poolsestimate)
        self.poolsestimate = poolsestimate

    def check_minblocks(self, poolsestimate):
        if len(poolsestimate.blockstats) < self.minblocks:
            # Ensure that not bool(poolsestimate), using the object's
            # __nonzero__ magic method.
            # This is to signal to the simulator that the estimate is
            # not ready.
            poolsestimate.blockrate = None

    def get_stats(self):
        stats = {
            "params": {"window": self.window,
                       "minblocks": self.minblocks},
        }
        est = self.poolsestimate
        if not est:
            return stats
        # minfeerates = sorted(est.minfeerates)
        # maxblocksizes = sorted(est.maxblocksizes)
        capfn = est.get_capacityfn()
        feerates, caps = zip(*capfn)
        stats.update({
            "feerates": feerates,
            "caps": caps,
            "blockinterval": 1 / est.blockrate
        })
        return stats

    def __nonzero__(self):
        # TODO: Deprecate this
        raise NotImplementedError
# #
# #
# #class _PoolsOnlineEstimator(object):
# #
# #    def __init__(self, window,
# #                 update_period=DEFAULT_UPDATE_PERIOD,
# #                 minblocks=DEFAULT_MINBLOCKS):
# #        self.window = window
# #        self.update_period = update_period
# #        self.minblocks = minblocks
# #        self.best_diff_interval = None
# #        self.block_shortfall = 0
# #        self.lock = threading.Lock()
# #        self.load_estimates()
# #
# #    def update_async(self, currheight, stopflag=None):
# #        '''Update pool estimates in a new thread.
# #
# #        To be called by main thread once every mempool poll period.
# #        '''
# #        if self.lock.locked():
# #            # Make sure only 1 estimation thread is running at a time.
# #            return None
# #
# #        if self.best_diff_interval:
# #            # Update the blockrate if the difficulty has changed.
# #            curr_diff_interval = currheight // DIFF_RETARGET_INTERVAL
# #            if curr_diff_interval > self.best_diff_interval:
# #                threading.Thread(
# #                    target=self._update_blockrate,
# #                    args=(currheight, curr_diff_interval)).start()
# #
# #        if time() >= self.next_update:
# #            t = threading.Thread(
# #                target=self._update_pools,
# #                args=(currheight, stopflag))
# #            t.start()
# #            return t
# #
# #        return None
# #
# #    def get_pools(self):
# #        # We use a getter for the pool estimate as a reminder that the
# #        # reference is subject to change by the update thread, so you
# #        # should bind it to another variable if you want to perform multiple
# #        # operations on the object.
# #        return self.poolsestimate
# #
# #    def get_stats(self):
# #        params = {
# #            'window': self.window,
# #            'update_period': self.update_period,
# #            'minblocks': self.minblocks,
# #        }
# #        stats = {
# #            'next_update': self.next_update,
# #            'best_diff_interval': self.best_diff_interval,
# #            'params': params
# #        }
# #        est = self.poolsestimate
# #        if not est:
# #            stats.update({'block_shortfall': self.block_shortfall})
# #            return stats
# #        totalhashrate = est.calc_totalhashrate()
# #        stats.update({
# #            'timestamp': est.timestamp,
# #            'blockinterval': 1/est.blockrate,
# #            'totalhashrate': totalhashrate
# #        })
# #        poolstats = {
# #            name: {
# #                'hashrate': pool.hashrate,
# #                'proportion': pool.hashrate / totalhashrate,
# #                'maxblocksize': pool.maxblocksize,
# #                'minfeerate': pool.minfeerate,
# #                'abovekn': pool.mfrstats['abovekn'],
# #                'belowkn': pool.mfrstats['belowkn'],
# #                'mfrmean': pool.mfrstats['mean'],
# #                'mfrstd': pool.mfrstats['std'],
# #                'mfrbias': pool.mfrstats['bias']
# #            }
# #            for name, pool in est.pools.items()
# #        }
# #        stats.update({'pools': poolstats})
# #        return stats
# #
# #    @logexceptions
# #    def _update_pools(self, currheight, stopflag):
# #        with self.lock:
# #            rangetuple = [currheight-self.window+1, currheight+1]
# #            have_heights = MemBlock.get_heights(
# #                blockrangetuple=rangetuple, dbfile=MEMBLOCK_DBFILE)
# #            if len(have_heights) >= self.minblocks:
# #                rangetuple[0] = max(rangetuple[0], min(have_heights))
# #            else:
# #                self.block_shortfall = self.minblocks - len(have_heights)
# #                retry_interval = (self.block_shortfall*
# #                                  EXPECTED_BLOCK_INTERVAL/2)
# #                logger.info("Only {} blocks out of required {}, "
# #                            "trying again in {}m.".
# #                            format(len(have_heights), self.minblocks,
# #                                   retry_interval/60))
# #                self.next_update = time() + retry_interval
# #                return
# #            self.next_update = time() + self.update_period
# #            poolsestimate = copy(self.poolsestimate)
# #            try:
# #                poolsestimate.start(
# #                    rangetuple, stopflag=stopflag, dbfile=MEMBLOCK_DBFILE)
# #            except StopIteration:
# #                return
# #            self.poolsestimate = poolsestimate
# #            self.best_diff_interval = max(
# #                self.poolsestimate.blocksmetadata) // DIFF_RETARGET_INTERVAL
# #
# #    @logexceptions
# #    def _update_blockrate(self, currheight, curr_diff_interval):
# #        with self.lock:
# #            poolsestimate = copy(self.poolsestimate)
# #            poolsestimate.calc_blockrate(height=currheight)
# #            self.poolsestimate = poolsestimate
# #            self.best_diff_interval = curr_diff_interval
# #            logger.info("Difficulty has changed; new blockrate is {}".
# #                        format(poolsestimate.blockrate))
# #
# #    def load_estimates(self):
# #        try:
# #            self.poolsestimate = load_obj(SAVEFILE)
# #            assert self.poolsestimate
# #            bestheight = max(self.poolsestimate.blocksmetadata)
# #        except Exception:
# #            logger.info("Unable to load saved pools; "
# #                        "starting from scratch.")
# #            self.poolsestimate = PoolsEstimator()
# #        else:
# #            if time() - self.poolsestimate.timestamp > self.update_period:
# #                logger.info("Loaded pool estimates are outdated; "
# #                            "starting from scratch.")
# #                self.poolsestimate.pools = {}
# #            else:
# #                logger.info("Pools Estimator loaded with best height %d." %
# #                            bestheight)
# #                self.best_diff_interval = (
# #                    bestheight // DIFF_RETARGET_INTERVAL)
# #        self.next_update = self.poolsestimate.timestamp + self.update_period
# #
# #    def save_estimates(self):
# #        try:
# #            save_obj(self.poolsestimate, SAVEFILE)
# #        except Exception:
# #            logger.warning("Unable to save pools.")
# #
# #    def __nonzero__(self):
# #        return bool(self.poolsestimate)
