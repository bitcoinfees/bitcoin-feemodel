from __future__ import division

import logging
from random import sample
from time import time
from math import log
from feemodel.util import round_random
from feemodel.config import memblock_dbfile
from feemodel.txmempool import MemBlock
from feemodel.simul.txsources import SimTxSource, SimTx

default_maxsamplesize = 10000
logger = logging.getLogger(__name__)


class ExpEstimator(SimTxSource):
    '''Continuous rate estimation with an exponential smoother.'''

    BATCH_INTERVAL = 30

    def __init__(self, halflife):
        '''Specify the halflife of the exponential decay, in seconds.'''
        self.halflife = halflife
        self._alpha = 0.5**(1 / halflife)
        self._reset_params()

    def start(self, blockheight, stopflag=None, dbfile=memblock_dbfile):
        self._reset_params()
        starttime = time()
        num_blocks_to_use = int(log(0.01) / log(self._alpha) / 600)
        _startblock = blockheight - num_blocks_to_use + 1
        blockrangetuple = (_startblock, blockheight+1)
        logger.info("Starting TxRate estimation "
                    "from blockrange ({}, {}).".format(*blockrangetuple))

        # Used for the update immediate post-init
        bestheight = 0
        besttime = 0
        bestblocktxids = None

        prevblock = None
        for height in range(*blockrangetuple):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            if block and prevblock and prevblock.height == height - 1:
                blocktxids = set(block.entries)
                newtxids = blocktxids - set(prevblock.entries)
                newentries = [block.entries[txid] for txid in newtxids]
                newentries.sort(key=lambda entry: entry.time)
                prevtime = prevblock.time
                txbatch = []
                for entry in newentries:
                    tx = SimTx(entry.feerate, entry.size)
                    txbatch.append(tx)
                    interval = entry.time - prevtime
                    if interval > self.BATCH_INTERVAL:
                        self.update_txs(txbatch, interval, is_init=True)
                        prevtime = entry.time
                        txbatch = []
                self.update_txs(
                    txbatch, max(block.time-prevtime, 0), is_init=True)
                bestheight = block.height
                bestblocktxids = blocktxids
                besttime = block.time
            prevblock = block

        if self.totaltime == 0:
            raise ValueError("Insufficient number of blocks.")
        self._calc_txrate()
        logger.info("Finished TxRate estimation in %.2f seconds." %
                    (time()-starttime))
        return bestheight, besttime, bestblocktxids

    def update_txs(self, new_txs, interval, is_init=False):
        '''Update the estimator with a new set of transactions.

        new_txs is a list of SimEntry objects and represents the new txs since
        the last update.

        interval is the time in seconds since the last update.
        '''
        self.totaltime += interval
        num_old_to_keep = round_random(
            len(self.txsample)*self._alpha**interval)
        self.txsample = sample(self.txsample, num_old_to_keep) + new_txs
        if not is_init:
            self._calc_txrate()

    def _calc_txrate(self):
        '''Calculate the tx rate (arrivals per second).'''
        if self.totaltime <= 0:
            raise ValueError("Insufficient number of blocks.")
        self.txrate = len(self.txsample) * log(self._alpha) / (
            self._alpha**self.totaltime - 1)

    def _reset_params(self):
        '''Reset the params; at init and upon (re)starting estimation.'''
        self.txsample = []
        self.txrate = None
        self.totaltime = 0

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class RectEstimator(SimTxSource):
    def __init__(self, maxsamplesize=default_maxsamplesize):
        self.maxsamplesize = maxsamplesize
        self._reset_params()

    def _reset_params(self):
        self.txsample = []
        self.txrate = None
        self.totaltime = 0.
        self.totaltxs = 0
        self.height = 0

    def start(self, blockrangetuple, stopflag=None, dbfile=memblock_dbfile):
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
                self.height = height
            prevblock = block
        if self.totaltxs < 0 or self.totaltime <= 0:
            raise ValueError("Insufficient number of blocks.")
        self.txrate = self.totaltxs / self.totaltime
        logger.info("Finished TxRate estimation in %.2f seconds." %
                    (time()-starttime))

    def _addblock(self, block, prevblock):
        newtxids = set(block.entries) - set(prevblock.entries)
        newtxs = [
            SimTx(block.entries[txid].feerate, block.entries[txid].size)
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
            self.txsample = (sample(self.txsample, numkeepold) +
                             sample(newtxs, numaddnew))

        self.totaltxs = newtotaltxs
        self.totaltime += block.time - prevblock.time
