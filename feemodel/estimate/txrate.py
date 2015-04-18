from __future__ import division

import logging
from random import sample
from time import time
from math import log
from feemodel.util import round_random
from feemodel.txmempool import MemBlock, MEMBLOCK_DBFILE
from feemodel.simul.txsources import SimTxSource, SimTx

DEFAULT_MAXSAMPLESIZE = 10000
logger = logging.getLogger(__name__)


class ExpEstimator(SimTxSource):
    '''Continuous rate estimation with an exponential smoother.'''

    BATCH_INTERVAL = 60

    def __init__(self, halflife):
        '''Specify the halflife of the exponential decay, in seconds.'''
        self.halflife = halflife
        self._alpha = 0.5**(1 / halflife)
        self._reset_params()

    def start(self, blockheight, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        self._reset_params()
        starttime = time()
        num_blocks_to_use = int(log(0.01) / log(self._alpha) / 600)
        _startblock = blockheight - num_blocks_to_use + 1
        blockrangetuple = (_startblock, blockheight+1)
        logger.info("Starting TxRate estimation "
                    "from blockrange ({}, {}).".format(*blockrangetuple))

        prevblock = None
        block = None
        for height in range(*blockrangetuple):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            try:
                blockdelta = block - prevblock
            except TypeError:
                # Either block or prevblock is None
                pass
            else:
                if blockdelta.height != 1:
                    continue
                newentries = sorted(blockdelta.entries.values(),
                                    key=lambda entry: entry.time)
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
            finally:
                prevblock = block

        if self.totaltime == 0:
            raise ValueError("Insufficient number of blocks.")
        self._calc_txrate()
        logger.info("Finished TxRate estimation in %.2f seconds." %
                    (time()-starttime))

        return block

    def update_txs(self, new_txs, interval, is_init=False):
        '''Update the estimator with a new set of transactions.

        new_txs is a list of SimTx instances which represent newly arrived txs
        since the last update.

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
            raise ValueError("Invalid total time.")
        self.txrate = len(self.txsample) * log(self._alpha) / (
            self._alpha**self.totaltime - 1)

    def _reset_params(self):
        '''Reset the params; at init and upon (re)starting estimation.'''
        self.txsample = []
        self.txrate = None
        self.totaltime = 0


class RectEstimator(SimTxSource):

    def __init__(self, maxsamplesize=DEFAULT_MAXSAMPLESIZE):
        self.maxsamplesize = maxsamplesize
        self._reset_params()

    def _reset_params(self):
        self.txsample = []
        self.txrate = None
        self.totaltime = 0.
        self.totaltxs = 0

    def start(self, blockrangetuple, stopflag=None, dbfile=MEMBLOCK_DBFILE):
        logger.info("Starting TxRate estimation "
                    "from blockrange ({}, {}).".format(*blockrangetuple))
        starttime = time()
        self._reset_params()
        prevblock = None
        for height in range(*blockrangetuple):
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            self._addblock(block, prevblock)
            prevblock = block
        if self.totaltxs < 0 or self.totaltime <= 0:
            raise ValueError("Insufficient number of blocks.")
        self.txrate = self.totaltxs / self.totaltime
        logger.info("Finished TxRate estimation in %.2f seconds." %
                    (time()-starttime))

    def _addblock(self, block, prevblock):
        try:
            blockdelta = block - prevblock
        except TypeError:
            # Either block or prevblock is None
            return
        if blockdelta.height != 1:
            return
        newtxs = [SimTx(entry.feerate, entry.size)
                  for entry in blockdelta.entries.values()]
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
        self.totaltime += blockdelta.time
