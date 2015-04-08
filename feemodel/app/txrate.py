'''Tx rate online estimation.'''

from __future__ import division

import logging
from copy import copy
from time import time
from feemodel.estimate import ExpEstimator
from feemodel.config import memblock_dbfile

default_halflife = 3600  # 1 hour

logger = logging.getLogger(__name__)


class TxRateOnlineEstimator(object):

    def __init__(self, halflife=default_halflife, dbfile=memblock_dbfile):
        self.dbfile = dbfile
        self.txrate_estimator = ExpEstimator(halflife)
        self.prevtxids = None
        self.prevtime = None

    def update(self, curr_entries, currheight):
        currtime = time()
        txrate_estimator = copy(self.txrate_estimator)
        if txrate_estimator.totaltime == 0:
            # Estimate not yet initialized.
            bestheight, besttime, bestblocktxids = txrate_estimator.start(
                currheight, dbfile=self.dbfile)
            if bestheight == currheight:
                logger.info("bestheight matches currheight.")
                self.prevtxids = bestblocktxids
                self.prevtime = besttime
        curr_txids = set(curr_entries)
        if self.prevtime:
            new_txids = curr_txids - self.prevtxids
            new_txs = [
                (curr_entries[txid].feerate, curr_entries[txid].size, '')
                for txid in new_txids]
            txrate_estimator.update_txs(new_txs, currtime - self.prevtime)
        self.prevtime = currtime
        self.prevtxids = curr_txids
        self.txrate_estimator = txrate_estimator

    def get_txsource(self):
        return self.txrate_estimator

    def __nonzero__(self):
        return bool(self.txrate_estimator)
