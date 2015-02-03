import logging
import threading
import os
from copy import deepcopy
from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, StoppableThread, proxy
from feemodel.estimate.pools import PoolsEstimator

logger = logging.getLogger(__name__)


class PoolsEstimatorOnline(StoppableThread):

    savedir = os.path.join(datadir, 'pools/')

    def __init__(self, window, update_period=144):
        try:
            self.load_pe()
        except:
            logger.error("Unable to load saved pools.")
            self.pe = PoolsEstimator()
            self.height = 0
        else:
            try:
                self.height = max(self.pe.blockmap)
            except:
                self.height = 0
            logger.info("Pools Estimator loaded with best height %d" %
                        self.height)

        if not os.path.exists(self.savedir):
            os.mkdir(self.savedir)
        self.window = window
        self.update_period = update_period
        self.pools_lock = threading.Lock()
        self._copy_cache()
        super(self.__class__, self).__init__()

    def run(self):
        logger.info("Starting pools online estimator.")
        try:
            while not self.is_stopped():
                self.update()
                self.sleep(600)
        except StopIteration:
            pass
        logger.info("Stopped pools online estimator.")

    def update(self):
        currheight = proxy.getblockcount()
        if currheight - self.height < self.update_period:
            return
        rangetuple = (currheight-self.window+1, currheight+1)
        try:
            self.pe.start(rangetuple, stopflag=self.get_stop_object())
        except ValueError:
            logger.exception("No pools estimated.")
        else:
            self.height = currheight
            try:
                self.save_pe()
            except:
                logger.exception("Unable to save pools.")
            self._copy_cache()

    def load_pe(self):
        savefiles = sorted(os.listdir(self.savedir))
        savefile = os.path.join(self.savedir, savefiles[-1])
        self.pe = load_obj(savefile)

    def save_pe(self):
        savefilename = 'pe' + str(self.height) + '.pickle'
        savefile = os.path.join(self.savedir, savefilename)
        save_obj(self.pe, savefile)

    def get_pools(self):
        with self.pools_lock:
            return self.pe_cached

    def _copy_cache(self):
        with self.pools_lock:
            self.pe_cached = deepcopy(self.pe)
