from __future__ import division

import logging
from time import time
from bisect import bisect

from feemodel.util import Table
from feemodel.config import prioritythresh

logger = logging.getLogger(__name__)


# TODO: change this to BlockScore
class BlockScore(object):
    def __init__(self, feerates):
        self.feerates = feerates
        self.numtxs = [0]*len(feerates)
        self.num_in = [0]*len(feerates)

    def score(self, feerate, is_in):
        fidx = bisect(self.feerates, feerate)
        if fidx > 0:
            self.numtxs[fidx-1] += 1
            self.num_in[fidx-1] += int(is_in)

    def __add__(self, other):
        if self.feerates != other.feerates:
            raise ValueError("Feerates of BlockScore add operands "
                             "must be equal.")
        totaltxs = [self_n + other_n
                    for self_n, other_n in zip(self.numtxs, other.numtxs)]
        totalin = [self_n + other_n
                   for self_n, other_n in zip(self.num_in, other.num_in)]
        result = BlockScore(self.feerates)
        result.numtxs = totaltxs
        result.num_in = totalin
        return result


class Prediction(object):
    def __init__(self, feerates, window):
        self.feerates = feerates
        self.window = window
        self.predicts = {}
        self.blockscores = {}
        self.scores = BlockScore(self.feerates)

    def update_predictions(self, entries, transientstats):
        new_txids = set(entries) - set(self.predicts)
        currtime = time()
        for txid in new_txids:
            entry = entries[txid]
            if not entry.depends and entry.currentpriority < prioritythresh:
                waittime = transientstats.predict(entry.feerate)
                if waittime is not None:
                    self.predicts[txid] = waittime + currtime
                    continue
            self.predicts[txid] = None

    def process_block(self, blocks):
        for block in blocks:
            numpredicts = 0
            block_predict = BlockScore(self.feerates)
            for txid, entry in block.entries.items():
                if entry.inblock:
                    predicted = self.predicts.get(txid)
                    if predicted:
                        is_in = predicted > block.time
                        block_predict.score(entry.feerate, is_in)
                        del self.predicts[txid]
                        numpredicts += 1
            self.blockscores[block.height] = block_predict
            for height in self.blockscores.keys():
                if height <= block.height - self.window:
                    del self.blockscores[height]
            logger.info("Block %d: %d predicts tallied." %
                        (block.height, numpredicts))

            # Remove from predictions those entries that are no longer
            # in the mempool for whatever reason.
            predicts_del = set(self.predicts) - set(block.entries)
            for txid in predicts_del:
                del self.predicts[txid]

        self.calc_score()

    def calc_score(self):
        self.scores = sum(self.blockscores.values(),
                          BlockScore(self.feerates))

    def print_scores(self):
        feerates = self.scores.feerates
        num_in = self.scores.num_in
        numtxs = self.scores.numtxs
        ratios = [n / d if d else -1 for n, d in zip(num_in, numtxs)]

        table = Table()
        table.add_row(('Feerate', 'Ratio', 'Num Txs'))
        for idx in range(len(feerates)):
            table.add_row((
                feerates[idx],
                '%.2f' % ratios[idx],
                numtxs[idx]))
        table.print_table()

    def get_stats(self):
        if not self:
            return None
        feerates = self.scores.feerates
        num_in = self.scores.num_in
        numtxs = self.scores.numtxs
        ratios = [n / d if d else -1 for n, d in zip(num_in, numtxs)]

        stats = {
            'blockrange': [min(self.blockscores), max(self.blockscores)],
            'feerates': feerates,
            'num_in': num_in,
            'num_txs': numtxs,
            'ratios': ratios
        }
        return stats

    def __nonzero__(self):
        return bool(self.blockscores)
