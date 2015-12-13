'''Tx rate online estimation.'''

from __future__ import division

import logging
from copy import copy

from feemodel.config import MINRELAYTXFEE
from feemodel.estimate import ExpEstimator

DEFAULT_HALFLIFE = 3600  # 1 hour

logger = logging.getLogger(__name__)


class TxRateOnlineEstimator(object):

    def __init__(self, txsource_init=None, halflife=DEFAULT_HALFLIFE):
        if txsource_init is not None and txsource_init.halflife != halflife:
            raise ValueError("Specified halflife does not"
                             "match with init txsource.")
        self.halflife = halflife
        self.tx_estimator = txsource_init

    def update(self, state):
        tx_estimator = copy(self.tx_estimator)
        if tx_estimator is None:
            tx_estimator = ExpEstimator(self.halflife)
            tx_estimator.start(state.height)
        tx_estimator.update(state)
        logger.debug(repr(tx_estimator))
        self.tx_estimator = tx_estimator

    def get_txsource(self):
        return self.tx_estimator

    def get_stats(self):
        stats = {
            "params": {"halflife": self.halflife},
        }
        est = self.tx_estimator
        if not est:
            return stats
        byteratefn = est.get_byteratefn()
        rate_with_fee = byteratefn(MINRELAYTXFEE)
        feerates, byterates = zip(*byteratefn.approx())

        meanbyterate, meanstd = est.calc_mean_byterate()
        stats.update({
            "samplesize": len(est.txsample),
            "txrate": est.txrate,
            "cumbyterate": {
                "feerates": feerates,
                "byterates": byterates
            },
            "expected_byterate": meanbyterate,
            "expected_byterate_std": meanstd,
            "ratewithfee": rate_with_fee,
            "totaltime": est.totaltime
        })
        return stats

    def __nonzero__(self):
        # TODO: Deprecate this
        raise NotImplementedError
        return bool(self.tx_estimator)
