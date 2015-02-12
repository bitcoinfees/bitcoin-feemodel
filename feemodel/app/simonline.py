from __future__ import division

import logging
import threading
import os
from time import time

from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, proxy
from feemodel.txmempool import TxMempool, MemBlock
from feemodel.app import SteadyStateOnline, TransientOnline
from feemodel.app import PoolsEstimatorOnline, Prediction

pools_window = 2016
pools_update_period = 129600  # 1.5 days

ss_window = 2016
ss_update_period = 86400  # Daily
ss_maxiters = 200000
ss_miniters = 100000
ss_maxtime = 3600

trans_window = 18
trans_update_period = 60
trans_maxiters = 10000
trans_miniters = 1000
trans_maxtime = 60

predict_feerates = range(0, 60000, 10000)
predict_window = 2016

logger = logging.getLogger(__name__)


class SimOnline(TxMempool):

    predict_savefile = os.path.join(datadir, 'savepredicts.pickle')

    def __init__(self):
        # TODO: Put minimum required history. Done
        # TODO: Remember to catch TERM

        self.process_lock = threading.Lock()
        self.peo = PoolsEstimatorOnline(pools_window,
                                        update_period=pools_update_period)
        self.ss = SteadyStateOnline(
            self.peo,
            ss_window,
            update_period=ss_update_period,
            miniters=ss_miniters,
            maxiters=ss_maxiters,
            maxtime=ss_maxtime)
        self.trans = TransientOnline(
            self,
            self.peo,
            trans_window,
            update_period=trans_update_period,
            miniters=trans_miniters,
            maxiters=trans_maxiters,
            maxtime=trans_maxtime)
        self.load_predicts()
        self.starttime = time()
        super(SimOnline, self).__init__()

    def run(self):
        with self.peo.thread_start(), self.ss.thread_start(), \
                self.trans.thread_start():
            super(SimOnline, self).run()

    def update(self):
        super(SimOnline, self).update()
        self.prediction.update_predictions(self.get_entries(),
                                           self.trans.stats)

    def process_blocks(self, *args, **kwargs):
        with self.process_lock:
            blocks = super(SimOnline, self).process_blocks(*args, **kwargs)
            self.prediction.process_block(blocks)
            try:
                save_obj(self.prediction, self.predict_savefile)
            except Exception:
                logger.exception("Unable to save predicts.")

    def get_status(self):
        runtime = time() - self.starttime
        currheight = proxy.getblockcount()
        numhistory = len(MemBlock.get_heights())
        peo_status = self.peo.status
        ss_status = self.ss.status
        trans_status = self.trans.status
        if self.rawmempool:
            mempool_status = 'running'
        else:
            mempool_status = 'stopped'

        status = {
            'runtime': runtime,
            'height': currheight,
            'numhistory': numhistory,
            'poolestimator': peo_status,
            'steadystate': ss_status,
            'transient': trans_status,
            'mempool': mempool_status}

        return status

    def load_predicts(self):
        try:
            self.prediction = load_obj(self.predict_savefile)
            assert self.prediction
        except Exception:
            logger.info("Unable to load saved predicts; "
                        "starting from scratch.")
            self.prediction = Prediction(predict_feerates, predict_window)
        else:
            # TODO: change the window.
            if self.prediction.feerates != predict_feerates:
                logger.info("Predict feerates have changed; "
                            "starting from scratch.")
                self.prediction = Prediction(predict_feerates, predict_window)
            else:
                numpredicts = len(self.prediction.predicts)
                blockscorerange = (min(self.prediction.blockscores),
                                   max(self.prediction.blockscores))
                logger.info("%d predicts loaded; "
                            "block scores in range %s loaded." %
                            (numpredicts, blockscorerange))
                self.prediction.window = predict_window
