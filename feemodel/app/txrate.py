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
            if state.height == lastblock.blockheight:
                logger.info("Init last height matches currheight.")
                self.prevstate = lastblock
            else:
                self.prevstate = copy(state)

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
            "numsamples": len(est.txsample),
            "txrate": est.txrate,
            "byterate": {
                "mean": meanbyterate,
                "mean_std": meanstd
            },
            "totaltime": est.totaltime
        }
        return stats

    def __nonzero__(self):
        return bool(self.tr_estimator)

    # def update(self, curr_entries, currheight):
    #     currtime = time()
    #     tr_estimator = copy(self.tr_estimator)
    #     if self.prevtime is None:
    #         try:
    #             bestheight, besttime, bestblocktxids = tr_estimator.start(
    #                 currheight, dbfile=MEMBLOCK_DBFILE)
    #         except ValueError:
    #             # There are no memblocks
    #             self.prevtxids = set(curr_entries)
    #             self.prevtime = currtime
    #             return
    #         else:
    #             if bestheight == currheight:
    #                 logger.info("bestheight matches currheight.")
    #                 self.prevtxids = bestblocktxids
    #                 self.prevtime = besttime
    #     curr_txids = set(curr_entries)
    #     if self.prevtime:
    #         new_txids = curr_txids - self.prevtxids
    #         new_txs = [
    #             SimTx(curr_entries[txid].feerate, curr_entries[txid].size)
    #             for txid in new_txids]
    #         tr_estimator.update_txs(new_txs, currtime - self.prevtime)
    #     self.prevtime = currtime
    #     self.prevtxids = curr_txids
    #     self.tr_estimator = tr_estimator
