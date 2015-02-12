from __future__ import division

import logging
from random import sample
from time import time
from feemodel.util import round_random
from feemodel.config import history_file
from feemodel.txmempool import MemBlock
from feemodel.simul import SimTxSource
from feemodel.simul.txsources import SimEntry

default_maxsamplesize = 10000
logger = logging.getLogger(__name__)


class TxRateEstimator(SimTxSource):
    def __init__(self, maxsamplesize=default_maxsamplesize):
        self.maxsamplesize = maxsamplesize
        self._reset_params()

    def _reset_params(self):
        self.txsample = []
        self.txrate = 0.
        self.totaltime = 0.
        self.totaltxs = 0
        self.height = 0

    def start(self, blockrangetuple, stopflag=None, dbfile=history_file):
        logger.info("Starting TxRate estimation "
                    "from blockrange ({}, {}).".format(*blockrangetuple))
        starttime = time()
        self._reset_params()
        prevblock = None
        for height in range(*blockrangetuple):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            if block and prevblock and prevblock.height == height - 1:
                self._addblock(block, prevblock)
            prevblock = block
        if self.totaltxs < 0 or self.totaltime <= 0:
            raise ValueError("Insufficient number of blocks.")
        self.txrate = self.totaltxs / self.totaltime
        for tx in self.txsample:
            tx._id = ''
            tx.depends = []
        self.height = blockrangetuple[1] - 1
        logger.info("Finished TxRate estimation in %.2f seconds." %
                    (time()-starttime))

    def _addblock(self, block, prevblock):
        newtxids = set(block.entries) - set(prevblock.entries)
        newtxs = [SimEntry.from_mementry(txid, block.entries[txid])
                  for txid in newtxids]
        newtotaltxs = self.totaltxs + len(newtxs)
        if newtotaltxs:
            oldprop = self.totaltxs / newtotaltxs
            combinedsize = min(self.maxsamplesize,
                               len(self.txsample)+len(newtxs))
            numkeepold = round_random(oldprop*combinedsize)
            if numkeepold > len(self.txsample):
                numkeepold = len(self.txsample)
                numaddnew = round_random(numkeepold/oldprop*(1-oldprop))
            elif combinedsize - numkeepold > len(newtxs):
                numaddnew = len(newtxs)
                numkeepold = round_random(numaddnew/(1-oldprop)*oldprop)
            else:
                numaddnew = combinedsize - numkeepold
            combinedsample = (sample(self.txsample, numkeepold) +
                              sample(newtxs, numaddnew))
        else:
            combinedsample = self.txsample

        self.totaltxs = newtotaltxs
        conflicts = [txid for txid, entry in block.entries.items()
                     if entry.isconflict]
        self.txsample = filter(lambda tx: tx._id not in conflicts,
                               combinedsample)
        self.totaltime += block.time - prevblock.time
        self.totaltxs -= len(conflicts)
        self.totaltxs = max(self.totaltxs, 0)
