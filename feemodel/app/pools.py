from __future__ import division

import logging
import threading
import os
from time import time
from copy import deepcopy
from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, StoppableThread, proxy
from feemodel.estimate.pools import PoolsEstimator

logger = logging.getLogger(__name__)

default_update_period = 86400


class PoolsEstimatorOnline(StoppableThread):

    savedir = os.path.join(datadir, 'pools/')

    def __init__(self, window, update_period=default_update_period):
        # TODO: make sure IOError is caught by owner
        self.pools_lock = threading.Lock()
        self.window = window
        self.update_period = update_period
        try:
            self.load_pe()
            assert self.pe
            bestheight = max(self.pe.blockmap)
        except:
            logger.error("Unable to load saved pools.")
            self.pe = PoolsEstimator()
        else:
            if time() - self.pe.timestamp > self.update_period:
                logger.info("Loaded pool estimates are outdated; "
                            "starting from scratch.")
                self.pe = PoolsEstimator()
            else:
                logger.info("Pools Estimator loaded with best height %d." %
                            bestheight)

        self.next_update = self.pe.timestamp + update_period
        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        super(self.__class__, self).__init__()

    def run(self):
        logger.info("Starting pools online estimator.")
        try:
            self.sleep(max(0, self.next_update-time()))
            while not self.is_stopped():
                self.update()
                self.sleep(max(0, self.next_update-time()))
        except StopIteration:
            pass
        logger.info("Stopped pools online estimator.")

    def update(self):
        currheight = proxy.getblockcount()
        pe = deepcopy(self.pe)
        rangetuple = (currheight-self.window+1, currheight+1)
        try:
            pe.start(rangetuple, stopflag=self.get_stop_object())
        except ValueError:
            # TODO: replace with custom error
            logger.exception("No pools estimated.")
        else:
            self.pe = pe
            self.next_update = pe.timestamp + self.update_period
            try:
                self.save_pe(currheight)
            except:
                logger.exception("Unable to save pools.")

    @property
    def pe(self):
        with self.pools_lock:
            return self._pe

    @pe.setter
    def pe(self, val):
        with self.pools_lock:
            self._pe = val

    def load_pe(self):
        savefiles = sorted(os.listdir(self.savedir))
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.pe = load_obj(savefile)

    def save_pe(self, currheight):
        savefilename = 'pe' + str(currheight) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.pe, savefile)
