from __future__ import division

import logging
import threading
import os
from time import time
from copy import deepcopy
from feemodel.config import datadir, history_file, DIFF_RETARGET_INTERVAL
from feemodel.util import save_obj, load_obj
from feemodel.txmempool import MemBlock
from feemodel.estimate.pools import PoolsEstimator

logger = logging.getLogger(__name__)

default_update_period = 86400
default_savedir = os.path.join(datadir, 'pools/')


class PoolsOnlineEstimator(object):

    def __init__(self, window,
                 update_period=default_update_period,
                 dbfile=history_file,
                 savedir=default_savedir):
        self.window = window
        self.update_period = update_period
        self.dbfile = dbfile
        self.savedir = savedir
        self.best_diff_interval = None
        self.lock = threading.Lock()
        try:
            self.load_estimates()
            assert self.poolsestimate
            bestheight = max(self.poolsestimate.blockmap)
        except Exception:
            logger.info("Unable to load saved pools; "
                        "starting from scratch.")
            self.poolsestimate = PoolsEstimator()
        else:
            if time() - self.poolsestimate.timestamp > self.update_period:
                logger.info("Loaded pool estimates are outdated; "
                            "starting from scratch.")
                self.poolsestimate.clear_pools()
            else:
                logger.info("Pools Estimator loaded with best height %d." %
                            bestheight)
                self.best_diff_interval = bestheight // DIFF_RETARGET_INTERVAL
        self.next_update = self.poolsestimate.timestamp + update_period
        if not os.path.exists(self.savedir):
            os.makedirs(self.savedir)

    def update_async(self, currheight, stopflag=None):
        '''Update pool estimates in a new thread.

        To be called by main thread once every mempool poll period.
        '''
        if self.lock.locked():
            # Make sure only 1 estimation thread is running at a time.
            return None

        if self.best_diff_interval:
            # Update the blockrate if the difficulty has changed.
            curr_diff_interval = currheight // DIFF_RETARGET_INTERVAL
            if curr_diff_interval > self.best_diff_interval:
                threading.Thread(
                    target=self._update_blockrate,
                    args=(currheight, curr_diff_interval)).start()

        if time() >= self.next_update:
            t = threading.Thread(
                target=self._update_pools,
                args=(currheight, stopflag))
            t.start()
            return t

        return None

    def get_pools(self):
        return self.poolsestimate

    def _update_pools(self, currheight, stopflag):
        with self.lock:
            self.next_update = time() + self.update_period
            rangetuple = [currheight-self.window+1, currheight+1]
            have_heights = MemBlock.get_heights(
                blockrangetuple=rangetuple, dbfile=self.dbfile)
            if have_heights:
                rangetuple[0] = max(rangetuple[0], min(have_heights))
            else:
                raise ValueError("Insufficient blocks.")
            poolsestimate = deepcopy(self.poolsestimate)
            try:
                poolsestimate.start(
                    rangetuple, stopflag=stopflag, dbfile=self.dbfile)
            except StopIteration:
                return
            self.poolsestimate = poolsestimate
            self.best_diff_interval = max(
                self.poolsestimate.blockmap) // DIFF_RETARGET_INTERVAL
            try:
                self.save_estimates(currheight)
            except Exception:
                logger.exception("Unable to save pools.")

    def _update_blockrate(self, currheight, curr_diff_interval):
        with self.lock:
            poolsestimate = deepcopy(self.poolsestimate)
            poolsestimate.calc_blockrate(currheight=currheight)
            self.poolsestimate = poolsestimate
            self.best_diff_interval = curr_diff_interval
            logger.info("Difficulty has changed; new blockrate is {}".
                        format(poolsestimate.blockrate))

    def load_estimates(self):
        savefiles = sorted([f for f in os.listdir(self.savedir)
                            if f.startswith('pe') and f.endswith('pickle')])
        # This works until ~ 2190 CE
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.poolsestimate = load_obj(savefile)

    def save_estimates(self, currheight):
        currheightstr = str(currheight)
        currheightstr = (7 - len(currheightstr)) * '0' + currheightstr
        savefilename = 'pe' + currheightstr + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.poolsestimate, savefile)

    def __nonzero__(self):
        return bool(self.poolsestimate)


# #class PoolsEstimatorOnline(StoppableThread):
# #
# #    savedir = os.path.join(datadir, 'pools/')
# #
# #    def __init__(self, window, update_period=default_update_period):
# #        self.pools_lock = threading.Lock()
# #        self.window = window
# #        self.update_period = update_period
# #        try:
# #            self.load_pe()
# #            assert self.pe
# #            bestheight = max(self.pe.blockmap)
# #        except Exception:
# #            logger.info("Unable to load saved pools; "
# #                        "starting from scratch.")
# #            self.pe = PoolsEstimator()
# #        else:
# #            if time() - self.pe.timestamp > self.update_period:
# #                logger.info("Loaded pool estimates are outdated; "
# #                            "starting from scratch.")
# #                self.pe.clear_pools()
# #            else:
# #                logger.info("Pools Estimator loaded with best height %d." %
# #                            bestheight)
# #
# #        self.next_update = self.pe.timestamp + update_period
# #        self._updating = None
# #        if not os.path.exists(self.savedir):
# #            os.mkdir(self.savedir)
# #        super(PoolsEstimatorOnline, self).__init__()
# #
# #    @StoppableThread.auto_restart(60)
# #    def run(self):
# #        try:
# #            logger.info("Starting pools online estimator.")
# #            logger.info("Windowfill is %.2f." % self._calc_windowfill())
# #            self.wait_for_resources()
# #            self._updating = False
# #            self.sleep_till_next()
# #            while not self.is_stopped():
# #                self.update()
# #                self.sleep_till_next()
# #        except StopIteration:
# #            pass
# #        finally:
# #            logger.info("Stopped pools online estimator.")
# #            self._updating = None
# #
# #    def wait_for_resources(self):
# #        '''Check and wait for all required resources to be ready.'''
# #        # TODO: move the windowfill checks into the main loop.
# #        while not self.is_stopped() and (
# #                self._calc_windowfill() < windowfillthresh):
# #            self.sleep(10)
# #
# #    def sleep_till_next(self):
# #        '''Sleep till the next update.'''
# #        self.sleep(max(0, self.next_update-time()))
# #
# #    def update(self):
# #        self._updating = True
# #        currheight = proxy.getblockcount()
# #        pe = deepcopy(self.pe)
# #        rangetuple = (currheight-self.window+1, currheight+1)
# #        pe.start(rangetuple, stopflag=self.get_stop_object())
# #        self.pe = pe
# #        self.next_update = pe.timestamp + self.update_period
# #        try:
# #            self.save_pe(currheight)
# #        except Exception:
# #            logger.exception("Unable to save pools.")
# #        self._updating = False
# #
# #    @property
# #    def pe(self):
# #        with self.pools_lock:
# #            return self._pe
# #
# #    @pe.setter
# #    def pe(self, val):
# #        with self.pools_lock:
# #            self._pe = val
# #
# #    def load_pe(self):
# #        savefiles = sorted([f for f in os.listdir(self.savedir)
# #                            if f.startswith('pe') and f.endswith('pickle')])
# #        savefile = os.path.join(self.savedir, savefiles[-1])
# #        self.pe = load_obj(savefile)
# #
# #    def save_pe(self, currheight):
# #        savefilename = 'pe' + str(currheight) + '.pickle'
# #        savefile = os.path.join(self.savedir, savefilename)
# #        save_obj(self.pe, savefile)
# #
# #    @property
# #    def status(self):
# #        if self._updating is None:
# #            return 'stopped'
# #        elif self._updating:
# #            return 'running'
# #        else:
# #            return 'idle'
# #
# #    def _calc_windowfill(self):
# #        '''Calculate window fill ratio.
# #
# #        Returns the ratio of the number of available memblocks within
# #        the window, to the window size.
# #        '''
# #        currheight = proxy.getblockcount()
# #        windowrange = (currheight-self.window+1, currheight+1)
# #        numblocks = len(MemBlock.get_heights(windowrange))
# #        return numblocks / self.window
