from __future__ import division

import logging
from random import sample
from time import time
from math import log, exp
from feemodel.util import round_random
from feemodel.config import history_file
from feemodel.txmempool import MemBlock
from feemodel.simul import SimTxSource

default_maxsamplesize = 10000
logger = logging.getLogger(__name__)


class TxRateEstimator(object):
    '''Dummy class to satisfy some imports.'''
    pass


class ExpEstimator(SimTxSource):
    '''Continuous rate estimation with an exponential smoother.'''

    def __init__(self, halflife):
        '''Specify the halflife of the exponential decay, in seconds.'''
        self.halflife = halflife
        self._a = -log(0.5) / halflife  # The exponent coefficient
        self._reset_params()

    def start(self, blockheight, stopflag=None, dbfile=history_file):
        self._reset_params()
        starttime = time()
        num_blocks_to_use = int(-log(0.01) / self._a / 600)
        startblock = blockheight - num_blocks_to_use + 1
        blockrangetuple = (startblock, blockheight+1)
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
                    # tx = SimEntry.from_mementry('', entry)
                    tx = (entry.feerate, entry.size, '')
                    txbatch.append(tx)
                    interval = entry.time - prevtime
                    if interval > 5:
                        self.update_txs(txbatch, interval, is_init=True)
                        prevtime = entry.time
                        txbatch = []
                self.update_txs(
                    txbatch, max(block.time-prevtime, 0), is_init=True)
                bestheight = block.height
                bestblocktxids = blocktxids
                besttime = block.time
                # new_txs = [SimEntry.from_mementry('', block.entries[txid])
                #            for txid in newtxids]
                # interval = block.time - prevblock.time
                # self.update_txs(new_txs, interval)
            prevblock = block

        if self.totaltime == 0:
            raise ValueError("Insufficient number of blocks.")
        self.calc_txrate()
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
            len(self._txsample)*self._decayfactor(interval))
        self._txsample = sample(self._txsample, num_old_to_keep) + new_txs
        if not is_init:
            self.calc_txrate()

    def calc_txrate(self):
        '''Calculate the tx rate (arrivals per second).'''
        if self.totaltime <= 0:
            raise ValueError("Insufficient number of blocks.")
        self.txrate = len(self._txsample) * self._a / (
            1 - exp(-self._a * self.totaltime))

    def _reset_params(self):
        '''Reset the params; at init and upon (re)starting estimation.'''
        self._txsample = []
        self.txrate = 0.
        self.totaltime = 0

    def _decayfactor(self, t):
        '''The decay factor as a function of time in seconds.'''
        return exp(-self._a*t)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class RectEstimator(SimTxSource):
    def __init__(self, maxsamplesize=default_maxsamplesize,
                 remove_conflicts=False):
        self.maxsamplesize = maxsamplesize
        self.remove_conflicts = remove_conflicts
        self._reset_params()

    def _reset_params(self):
        self._txsample = []
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
                self.height = height
            prevblock = block
        if self.totaltxs < 0 or self.totaltime <= 0:
            raise ValueError("Insufficient number of blocks.")
        self.txrate = self.totaltxs / self.totaltime
        self._txsample = [(tx[0], tx[1], '') for tx in self._txsample]
        logger.info("Finished TxRate estimation in %.2f seconds." %
                    (time()-starttime))

    def _addblock(self, block, prevblock):
        newtxids = set(block.entries) - set(prevblock.entries)
        # newtxs = [SimEntry.from_mementry(txid, block.entries[txid])
        #           for txid in newtxids]
        newtxs = [
            (block.entries[txid].feerate, block.entries[txid].size, txid)
            for txid in newtxids]
        newtotaltxs = self.totaltxs + len(newtxs)
        if newtotaltxs:
            oldprop = self.totaltxs / newtotaltxs
            combinedsize = min(self.maxsamplesize,
                               len(self._txsample)+len(newtxs))
            numkeepold = round_random(oldprop*combinedsize)
            if numkeepold > len(self._txsample):
                numkeepold = len(self._txsample)
                numaddnew = round_random(numkeepold/oldprop*(1-oldprop))
            elif combinedsize - numkeepold > len(newtxs):
                numaddnew = len(newtxs)
                numkeepold = round_random(numaddnew/(1-oldprop)*oldprop)
            else:
                numaddnew = combinedsize - numkeepold
            combinedsample = (sample(self._txsample, numkeepold) +
                              sample(newtxs, numaddnew))
        else:
            combinedsample = self._txsample

        self.totaltxs = newtotaltxs
        self.totaltime += block.time - prevblock.time
        if self.remove_conflicts:
            conflicts = [txid for txid, entry in block.entries.items()
                         if entry.isconflict]
            self._txsample = filter(lambda tx: tx[2] not in conflicts,
                                    combinedsample)
            self.totaltxs -= len(conflicts)
            self.totaltxs = max(self.totaltxs, 0)
        else:
            self._txsample = combinedsample
