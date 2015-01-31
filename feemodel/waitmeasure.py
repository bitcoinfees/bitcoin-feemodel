'''Measurement of average transaction wait times as a function of fee rate.

The wait time of a transaction is the time difference between entry to
mempool and inclusion in a block. The mempool entry time is measured by
Bitcoin Core and obtained via getrawmempool. The block time is measured by
feemodel.TxMempool and thus the error is at least on the order of the
poll_period.

The goal is to use the measured wait times as a baseline comparison for
simulated wait times; any error that is small relative to the typical block
interval of 10 min is acceptable.
'''
import logging
from bisect import bisect
from time import time
from feemodel import MemBlock
from feemodel.util import Table
from feemodel.config import prioritythresh
from feemodel.config import history_file

logger = logging.getLogger(__name__)


class WaitBlock(object):
    def __init__(self, feerates, txs=None, blocktime=None):
        self.feerates = feerates
        self.numtxs = [0]*len(feerates)

        if txs and blocktime:
            totalwaits = [0.]*len(feerates)
            for tx in txs:
                fidx = bisect(self.feerates, tx.feerate)
                if fidx > 0:
                    self.numtxs[fidx-1] += 1
                    totalwaits[fidx-1] += blocktime - tx.time

            self.avgwaits = [float(totwait) / n if n else 0.
                             for totwait, n in zip(totalwaits, self.numtxs)]
        else:
            self.avgwaits = [0.]*len(feerates)

    def print_waits(self):
        table = Table()
        table.add_row(('Feerates', 'Mean wait', 'Num txs'))
        for idx in range(len(self.feerates)):
            table.add_row((
                self.feerates[idx],
                '%.2f' % self.avgwaits[idx],
                self.numtxs[idx]))
        table.print_table()

    def __add__(self, other):
        if self.feerates != other.feerates:
            raise ValueError("Feerates of WaitBlock add operands "
                             "must be equal.")
        combinedwaits = []
        combinednumtxs = []
        for idx in range(len(self.feerates)):
            totaltxs = self.numtxs[idx] + other.numtxs[idx]
            if totaltxs:
                totalwait = (self.avgwaits[idx]*self.numtxs[idx] +
                             other.avgwaits[idx]*other.numtxs[idx])
                combinedwaits.append(totalwait/totaltxs)
            else:
                combinedwaits.append(0.)
            combinednumtxs.append(totaltxs)

        result = WaitBlock(self.feerates)
        result.avgwaits = combinedwaits
        result.numtxs = combinednumtxs

        return result

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class WaitMeasure(object):
    def __init__(self, feerates):
        self.feerates = feerates
        self._blacklist = set()
        self._waitblocks = {}

    def calcwaits(self, blockrangetuple, stopflag=None, dbfile=history_file):
        logger.info("Measuring wait times in blockrange ({}, {})".format(
            *blockrangetuple))
        starttime = time()
        for height in range(*blockrangetuple):
            if height in self._waitblocks:
                continue
            if stopflag and stopflag.is_set():
                raise StopIteration("Stop flag set.")
            block = MemBlock.read(height, dbfile=dbfile)
            if not block:
                continue
            blocktxids = set(block.entries)
            self._blacklist = self._blacklist & blocktxids
            whitelist = blocktxids - self._blacklist

            addlist = []
            for txid in whitelist:
                entry = block.entries[txid]
                if self._counttx(entry):
                    addlist.append(entry)
                elif self._toblacklist(entry):
                    self._blacklist.add(txid)

            self._waitblocks[height] = WaitBlock(self.feerates, addlist,
                                                 block.time)

        for height in self._waitblocks.keys():
            if height < blockrangetuple[0] or height >= blockrangetuple[1]:
                del self._waitblocks[height]

        logger.info("Completed wait times measure in %.2f seconds." %
                    (time() - starttime))
        return sum(self._waitblocks.values(), WaitBlock(self.feerates))

    @staticmethod
    def _counttx(entry):
        return (
            entry.inblock and
            not entry.depends and
            entry.currentpriority < prioritythresh
        )

    @staticmethod
    def _toblacklist(entry):
        return not entry.inblock and entry.depends

    def __eq__(self, other):
        return self.__dict__ == other.__dict__
