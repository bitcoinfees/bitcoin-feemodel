'''Tx rate online estimation.'''

from __future__ import division

import logging
from copy import copy
from time import time
from feemodel.estimate import ExpEstimator
from feemodel.config import history_file
from feemodel.simul import SimEntry

default_halflife = 3600  # 1 hour

logger = logging.getLogger(__name__)


class TxRateOnlineEstimator(object):

    def __init__(self, halflife=default_halflife, dbfile=history_file):
        self.dbfile = dbfile
        self.tr = ExpEstimator(halflife)

    def update(self, curr_entries, currheight):
        currtime = time()
        tr = copy(self.tr)
        if tr.totaltime == 0:
            # Estimate not yet initialized.
            bestheight, besttime, bestblocktxids = tr.start(
                currheight, dbfile=self.dbfile)
            if bestheight == currheight:
                logger.debug("bestheight matches currheight.")
                self.prevtxids = bestblocktxids
                self.prevtime = besttime
            else:
                self.prevtxids = None
                self.prevtime = None
        curr_txids = set(curr_entries)
        if self.prevtime:
            new_txids = curr_txids - self.prevtxids
            new_txs = [SimEntry.from_mementry('', curr_entries[txid])
                       for txid in new_txids]
            tr.update_txs(new_txs, currtime - self.prevtime)
        self.prevtime = currtime
        self.prevtxids = curr_txids
        self.tr = tr
