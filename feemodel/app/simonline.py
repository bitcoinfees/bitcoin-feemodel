from __future__ import division

import os
import logging

from feemodel.txmempool import TxMempool, MEMBLOCK_DBFILE
from feemodel.config import (datadir, pools_config, txrate_halflife,
                             trans_config, predict_config)
from feemodel.util import load_obj, save_obj, WorkerThread
from feemodel.app.pools import PoolsOnlineEstimator
from feemodel.app.txrate import TxRateOnlineEstimator
from feemodel.app.transient import TransientOnline
from feemodel.app.predict import Prediction, PVALS_DBFILE

logger = logging.getLogger(__name__)
PREDICT_SAVEFILE = os.path.join(datadir, 'savepredict.pickle')


class SimOnline(TxMempool):

    def __init__(self):
        super(SimOnline, self).__init__(dbfile=MEMBLOCK_DBFILE)
        self.predictworker = WorkerThread(self.update_predicts)
        self.load_predicts()

        self.poolsonline = PoolsOnlineEstimator(
            pools_config['window'],
            update_period=pools_config['update_period'],
            minblocks=pools_config['minblocks'])
        self.txonline = TxRateOnlineEstimator(halflife=txrate_halflife)
        self.transient = TransientOnline(
            self,
            self.poolsonline,
            self.txonline,
            update_period=trans_config['update_period'],
            miniters=trans_config['miniters'],
            maxiters=trans_config['maxiters'])

    def run(self):
        with self.transient.context_start():
            self.predictworker.start()
            super(SimOnline, self).run()
            self.predictworker.stop()
        self.save_predicts()

    def update(self):
        state = super(SimOnline, self).update()
        self.poolsonline.update_async(
            state.height, stopflag=self.get_stop_object())
        self.predictworker.put(state, self.transient.stats)
        self.txonline.update(state)

    def process_blocks(self, *args):
        memblocks = super(SimOnline, self).process_blocks(*args)
        self.predictworker.put(memblocks)

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
                predict_config['block_halflife'],
                blocks_to_keep=predict_config['blocks_to_keep'])
        else:
            logger.info("Prediction loaded with {} saved predicts.".
                        format(len(self.prediction.predicts)))

    def save_predicts(self):
        try:
            save_obj(self.prediction, PREDICT_SAVEFILE)
        except Exception:
            logger.warning("Unable to save predicts.")
