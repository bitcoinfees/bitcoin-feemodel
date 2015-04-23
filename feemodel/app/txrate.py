'''Tx rate online estimation.'''

from __future__ import division

import logging
from copy import copy

from feemodel.estimate import ExpEstimator

DEFAULT_HALFLIFE = 3600  # 1 hour

logger = logging.getLogger(__name__)


class TxRateOnlineEstimator(object):

    def __init__(self, halflife=DEFAULT_HALFLIFE):
        self.tx_estimator = ExpEstimator(halflife)

    def update(self, state):
        tx_estimator = copy(self.tx_estimator)
        if not tx_estimator:
            tx_estimator.start(state.height)
        tx_estimator.update(state)
        logger.debug(repr(tx_estimator))
        self.tx_estimator = tx_estimator

    def get_txsource(self):
        return self.tx_estimator

    def get_stats(self):
        est = self.tx_estimator
        if not est:
            return None
        meanbyterate, meanstd = est.calc_mean_byterate()
        stats = {
            "halflife": est.halflife,
            "samplesize": len(est.txsample),
            "txrate": est.txrate,
            "expected_byterate": meanbyterate,
            "expected_byterate_std": meanstd,
            "totaltime": est.totaltime
        }
        return stats

    def __nonzero__(self):
        return bool(self.tx_estimator)
