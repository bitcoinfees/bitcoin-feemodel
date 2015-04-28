'''Tx rate online estimation.'''

from __future__ import division

import logging
from copy import copy

from feemodel.config import MINRELAYTXFEE
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

    def get_stats(self, max_rate_delta=50, min_num_pts=20):
        est = self.tx_estimator
        stats = {
            "params": {"halflife": est.halflife},
        }
        if not est:
            return stats
        _feerates, _byterates = est.get_byterates()
        totalrate = _byterates[0]
        rate_delta = min(max_rate_delta, totalrate // min_num_pts)

        next_rate = rate_delta
        feerates = []
        byterates = []
        rate_with_fee = 0
        for feerate, byterate in reversed(zip(_feerates, _byterates)):
            if feerate >= MINRELAYTXFEE:
                rate_with_fee = byterate
            if byterate < next_rate:
                continue
            byterates.append(byterate)
            feerates.append(feerate)
            next_rate = min(rate_delta + byterate, totalrate)
        feerates.reverse()
        byterates.reverse()

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
        return bool(self.tx_estimator)
