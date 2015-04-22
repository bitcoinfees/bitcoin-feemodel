'''Tx rate online estimation.'''

from __future__ import division

import logging
from copy import copy

from feemodel.estimate import ExpEstimator
from feemodel.simul.txsources import SimTx

DEFAULT_HALFLIFE = 3600  # 1 hour

logger = logging.getLogger(__name__)


class TxRateOnlineEstimator(object):

    def __init__(self, halflife=DEFAULT_HALFLIFE):
        self.tr_estimator = ExpEstimator(halflife)
        self.prevstate = None

    def update(self, state):
        tr_estimator = copy(self.tr_estimator)
        if self.prevstate is None:
            self.init_calcs(state, tr_estimator)

        state_delta = state - self.prevstate
        newtxs = [SimTx(entry.feerate, entry.size)
                  for entry in state_delta.entries.values()]
        tr_estimator.update_txs(newtxs, state_delta.time)
        logger.debug(repr(tr_estimator))

        self.prevstate = state
        self.tr_estimator = tr_estimator

    def init_calcs(self, state, tr_estimator):
        logger.info("Beginning init calcs.")
        try:
            lastblock = tr_estimator.start(state.height)
        except ValueError:
            # There are no memblocks.
            self.prevstate = copy(state)
        else:
            logger.info(repr(tr_estimator))
            self.prevstate = lastblock if lastblock else copy(state)

        if state.time == self.prevstate.time:
            # Makes things a bit neater by guaranteeing that the first update
            # will never encounter a ValueError due to zero totaltime.
            self.prevstate.time -= 1

    def get_txsource(self):
        return self.tr_estimator

    def get_stats(self):
        est = self.tr_estimator
        if not est:
            return None
        meanbyterate, meanstd = est.calc_mean_byterate()
        stats = {
            "halflife": est.halflife,
            "samplesize": len(est.txsample),
            "txrate": est.txrate,
            "expected_byterate": meanbyterate,
            "expected_byterate_err": meanstd,
            "totaltime": est.totaltime
        }
        return stats

    def __nonzero__(self):
        return bool(self.tr_estimator)
