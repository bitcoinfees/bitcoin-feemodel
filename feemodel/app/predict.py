from __future__ import division

import logging
from time import time
from bisect import bisect
from math import ceil

from tabulate import tabulate

from feemodel.util import Function

logger = logging.getLogger(__name__)

# Should not include 0. Equi-spacing not necessary.
WAIT_PERCENTILE_PTS = [0.05*i for i in range(1, 21)]
NUM_PVAL_POINTS = 100
WAIT_MEDIAN_IDX = bisect(WAIT_PERCENTILE_PTS, 0.5) - 1


class TxPrediction(Function):
    '''Wait time prediction for a tx.

    The prediction is encoded in a function mapping wait times to p-values.
    When the tx enters a block, the p-value of the observed wait time is
    computed and aggregated.

    Under the model (i.e. null hypothesis), the p-value is uniformly
    distributed. By comparing the empirical distribution of p-values to
    the uniform one, we can measure the model validity.
    '''

    _y = [1 - w for w in [0] + WAIT_PERCENTILE_PTS]

    def __init__(self, waitpercentiles, feerate, entrytime):
        self._x = [0] + waitpercentiles
        self.feerate = feerate
        self.entrytime = entrytime
        self.median_waittime = waitpercentiles[WAIT_MEDIAN_IDX]
        self.waittime = None
        self.pval = None

    def calc_pval(self, blocktime):
        '''Calculate the p-value of the tx.

        blocktime is the timestamp of the block that included the tx.
        '''
        self.waittime = blocktime - self.entrytime
        self.pval = self(self.waittime, use_upper=True, use_lower=True)
        return self.pval


class Prediction(object):

    def __init__(self, block_halflife):
        self.block_halflife = block_halflife
        self._alpha = 0.5**(1 / block_halflife)
        self.pvalcounts = [0.]*NUM_PVAL_POINTS
        self.pval_ecdf = None
        self.pdistance = None
        self.predicts = {}

    def update_predictions(self, entries, transientstats):
        new_txids = set(entries) - set(self.predicts)
        currtime = time()
        for txid in new_txids:
            entry = entries[txid]
            if not entry.depends and not entry.is_high_priority():
                self.predicts[txid] = transientstats.predict(entry, currtime)
            else:
                self.predicts[txid] = None

    def process_block(self, blocks):
        for block in blocks:
            if block is None:
                continue
            newtxpredicts = []
            numpredicts = 0
            for txid, entry in block.entries.items():
                if entry.inblock:
                    if txid in self.predicts:
                        txpredict = self.predicts[txid]
                        if txpredict:
                            txpredict.calc_pval(block.time)
                            newtxpredicts.append(txpredict)
                            numpredicts += 1
                        del self.predicts[txid]
            self._add_pvals(newtxpredicts)
            logger.info("Block %d: %d predicts tallied." %
                        (block.height, numpredicts))

            # Remove from predictions those entries that are no longer
            # in the mempool. This can happen, for example, if the predicts
            # are outdated, or if a tx was removed as a conflict.
            predicts_del = set(self.predicts) - set(block.entries)
            for txid in predicts_del:
                del self.predicts[txid]

        self._calc_pval_ecdf()
        self._calc_pdistance()

    def print_predicts(self):
        '''Print the pval ECDF and predict-distance.'''
        headers = ['x', 'F(x)']
        table = zip(
            [i / NUM_PVAL_POINTS for i in range(1, NUM_PVAL_POINTS+1)],
            self.pval_ecdf)
        print("ECDF of p-values")
        print("================")
        print(tabulate(table, headers=headers))
        print("Halflife: {} blocks.".format(self.block_halflife))
        print("Predict-distance: {}".format(self.pdistance))

    def _add_pvals(self, txpredicts):
        new_pvalcounts = [0.]*NUM_PVAL_POINTS
        for tx in txpredicts:
            pval = tx.pval
            pvalcount_idx = max(int(ceil(pval*NUM_PVAL_POINTS)) - 1, 0)
            new_pvalcounts[pvalcount_idx] += 1
        for idx in range(len(self.pvalcounts)):
            self.pvalcounts[idx] = (self._alpha*self.pvalcounts[idx] +
                                    (1 - self._alpha)*new_pvalcounts[idx])

    def _calc_pval_ecdf(self):
        totalcount = sum(self.pvalcounts)
        self.pval_ecdf = []
        cumsum = 0.
        for count in self.pvalcounts:
            cumsum += count
            self.pval_ecdf.append(cumsum / totalcount)

    def _calc_pdistance(self):
        '''Calculate the predict-distance.

        This is the Kolmogorov-Smirnov distance between the p-value
        empirical CDF and the uniform distribution.

        We're not doing a KS test however, since the transactions have
        dependence and we don't know how to account for that.

        But it's still a useful metric for gauging the model validity.
        A pdistance of d implies that on average, when using the model
        to give a prediction bound W of level L on the wait time w of a tx,
        the probability P that the tx will be within the bound (i.e. w <= W)
        satisfies abs(P - L) <= d.
        '''
        d = [abs(pr - (idx+1)/NUM_PVAL_POINTS)
             for idx, pr in enumerate(self.pval_ecdf)]
        self.pdistance = max(d)


# #class BlockScore(object):
# #    def __init__(self, feerates):
# #        self.feerates = feerates
# #        self.numtxs = [0]*len(feerates)
# #        self.num_in = [0]*len(feerates)
# #
# #    def score(self, feerate, is_in):
# #        fidx = bisect(self.feerates, feerate)
# #        if fidx > 0:
# #            self.numtxs[fidx-1] += 1
# #            self.num_in[fidx-1] += int(is_in)
# #
# #    def __add__(self, other):
# #        if self.feerates != other.feerates:
# #            raise ValueError("Feerates of BlockScore add operands "
# #                             "must be equal.")
# #        totaltxs = [self_n + other_n
# #                    for self_n, other_n in zip(self.numtxs, other.numtxs)]
# #        totalin = [self_n + other_n
# #                   for self_n, other_n in zip(self.num_in, other.num_in)]
# #        result = BlockScore(self.feerates)
# #        result.numtxs = totaltxs
# #        result.num_in = totalin
# #        return result
# #
# #
# #class Prediction(object):
# #    def __init__(self, feerates, window):
# #        self.feerates = feerates
# #        self.window = window
# #        self.predicts = {}
# #        self.blockscores = {}
# #        self.scores = BlockScore(self.feerates)
# #
# #    def update_predictions(self, entries, transientstats):
# #        new_txids = set(entries) - set(self.predicts)
# #        currtime = time()
# #        for txid in new_txids:
# #            entry = entries[txid]
# #            if not entry.depends and not entry.is_high_priority():
# #                waittime = transientstats.predict(entry.feerate)
# #                if waittime is not None:
# #                    self.predicts[txid] = waittime + currtime
# #                    continue
# #            self.predicts[txid] = None
# #
# #    def process_block(self, blocks):
# #        for block in blocks:
# #            numpredicts = 0
# #            block_predict = BlockScore(self.feerates)
# #            for txid, entry in block.entries.items():
# #                if entry.inblock:
# #                    predicted = self.predicts.get(txid)
# #                    if predicted:
# #                        is_in = predicted > block.time
# #                        block_predict.score(entry.feerate, is_in)
# #                        del self.predicts[txid]
# #                        numpredicts += 1
# #            self.blockscores[block.height] = block_predict
# #            for height in self.blockscores.keys():
# #                if height <= block.height - self.window:
# #                    del self.blockscores[height]
# #            logger.info("Block %d: %d predicts tallied." %
# #                        (block.height, numpredicts))
# #
# #            # Remove from predictions those entries that are no longer
# #            # in the mempool for whatever reason.
# #            predicts_del = set(self.predicts) - set(block.entries)
# #            for txid in predicts_del:
# #                del self.predicts[txid]
# #
# #        self.calc_score()
# #
# #    def calc_score(self):
# #        self.scores = sum(self.blockscores.values(),
# #                          BlockScore(self.feerates))
# #
# #    def print_scores(self):
# #        feerates = self.scores.feerates
# #        num_in = self.scores.num_in
# #        numtxs = self.scores.numtxs
# #        ratios = [n / d if d else -1 for n, d in zip(num_in, numtxs)]
# #
# #        table = Table()
# #        table.add_row(('Feerate', 'Ratio', 'Num Txs'))
# #        for idx in range(len(feerates)):
# #            table.add_row((
# #                feerates[idx],
# #                '%.2f' % ratios[idx],
# #                numtxs[idx]))
# #        table.print_table()
# #
# #    def get_stats(self):
# #        if not self:
# #            return None
# #        feerates = self.scores.feerates
# #        num_in = self.scores.num_in
# #        numtxs = self.scores.numtxs
# #        ratioscores = [n / d if d else -1 for n, d in zip(num_in, numtxs)]
# #
# #        stats = {
# #            'blockrange': [min(self.blockscores), max(self.blockscores)],
# #            'feerates': feerates,
# #            'num_in': num_in,
# #            'num_txs': numtxs,
# #            'scores': ratioscores
# #        }
# #        return stats
# #
# #    def __nonzero__(self):
# #        return bool(self.blockscores)
