from __future__ import division

import os
import logging

from feemodel.txmempool import TxMempool
from feemodel.config import datadir, config
from feemodel.util import load_obj, save_obj, logexceptions, WorkerThread
import feemodel.util
from feemodel.app.pools import PoolsOnlineEstimator
from feemodel.app.txrate import TxRateOnlineEstimator
from feemodel.app.transient import TransientOnline
from feemodel.app.predict import Prediction, PVALS_DBFILE

logger = logging.getLogger(__name__)
PREDICT_SAVEFILE = os.path.join(datadir, 'savepredict.pickle')


class SimOnline(TxMempool):

    def __init__(self, txsource_init=None):
        super(SimOnline, self).__init__()
        self.predictworker = WorkerThread(self.update_predicts)
        self.load_predicts()

        currheight = feemodel.util.proxy.getblockcount()
        self.poolsonline = PoolsOnlineEstimator(
            currheight,
            self.get_stop_object(),
            config.getint("app", "pools_window"),
            minblocks=config.getint("app", "pools_minblocks"))
        self.txonline = TxRateOnlineEstimator(
            txsource_init=txsource_init,
            halflife=config.getint("app", "txrate_halflife"))

        trans_numprocesses = config.getint("app", "trans_numprocesses")
        if trans_numprocesses == -1:
            trans_numprocesses = None
        self.transient = TransientOnline(
            self,
            self.poolsonline,
            self.txonline,
            update_period=config.getint("app", "trans_update_period"),
            miniters=config.getint("app", "trans_miniters"),
            maxiters=config.getint("app", "trans_maxiters"),
            numprocesses=trans_numprocesses)

    @logexceptions
    def run(self):
        with self.transient.context_start():
            self.predictworker.start()
            super(SimOnline, self).run()
            self.predictworker.stop()
            self.save_predicts()
            self.poolsonline.save_estimates()

    def update(self):
        state = super(SimOnline, self).update()
        self.predictworker.put(state, self.transient.stats)
        self.txonline.update(state)

    def process_blocks(self, *args):
        memblocks = super(SimOnline, self).process_blocks(*args)
        self.predictworker.put(memblocks)
        self.poolsonline.update(memblocks)

    def update_predicts(self, *args):
        if len(args) == 2:
            self.prediction.update_predictions(*args)
        else:
            self.prediction.process_blocks(args[0], dbfile=PVALS_DBFILE)
            self.save_predicts()

    def get_predictstats(self):
        return self.prediction.get_stats()

    def get_transientstats(self):
        return self.transient.get_stats()

    def get_poolstats(self):
        return self.poolsonline.get_stats()

    def get_txstats(self):
        return self.txonline.get_stats()

    def load_predicts(self):
        try:
            self.prediction = load_obj(PREDICT_SAVEFILE)
        except Exception:
            logger.info("Unable to load saved predicts; "
                        "starting from scratch.")
            self.prediction = Prediction(
                config.getint("app", "predict_block_halflife"),
                blocks_to_keep=config.getint("app", "predict_blocks_to_keep"))
        else:
            logger.info("Prediction loaded with {} saved predicts.".
                        format(len(self.prediction.predicts)))

    def save_predicts(self):
        try:
            save_obj(self.prediction, PREDICT_SAVEFILE)
        except Exception:
            logger.warning("Unable to save predicts.")
