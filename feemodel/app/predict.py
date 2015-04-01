from __future__ import division

import os
import logging
import sqlite3
from time import time
from bisect import bisect
from math import ceil

from tabulate import tabulate

from feemodel.util import Function
from feemodel.config import datadir

logger = logging.getLogger(__name__)

# Should not include 0. Equi-spacing not necessary.
WAIT_PERCENTILE_PTS = [0.05*i for i in range(1, 21)]
NUM_PVAL_POINTS = 100
WAIT_MEDIAN_IDX = bisect(WAIT_PERCENTILE_PTS, 0.5) - 1

PVALS_DB_SCHEMA = {
    'txs': [
        'blockheight INTEGER',
        'entrytime INTEGER',
        'feerate INTEGER',
        'median_wait INTEGER',
        'waittime INTEGER',
        'pval REAL'
    ]
}
pvals_dbfile = os.path.join(datadir, 'pvals.db')
pvals_blocks_to_keep = 2016


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

    def __init__(self, feerate, entrytime, waitpercentiles=None):
        if waitpercentiles:
            self._x = [0] + waitpercentiles
            self.median_waittime = int(waitpercentiles[WAIT_MEDIAN_IDX])
        else:
            self._x = []
            self.median_waittime = None

        self.feerate = int(feerate)
        self.entrytime = int(entrytime)
        self.waittime = None
        self.pval = None

    def calc_pval(self, blocktime):
        '''Calculate the p-value of the tx.

        blocktime is the timestamp of the block that included the tx.
        This method only works when waitpercentiles was specified in
        __init__.
        '''
        self.waittime = int(blocktime) - self.entrytime
        self.pval = self(self.waittime, use_upper=True, use_lower=True)
        return self.pval

    def _get_attr_tuple(self):
        '''For writing to db.'''
        attr_tuple = (
            self.feerate,
            self.entrytime,
            self.median_waittime,
            self.waittime,
            self.pval)
        return attr_tuple

    @classmethod
    def _from_attr_tuple(cls, tup):
        tx = cls(tup[0], tup[1])
        tx.median_waittime = tup[2]
        tx.waittime = tup[3]
        tx.pval = tup[4]
        return tx


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
                self.predicts[txid] = transientstats.predict(entry.feerate,
                                                             currtime)
            else:
                self.predicts[txid] = None

    def process_block(self, blocks, dbfile=None):
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
            if dbfile:
                self._write_block(
                    block.height, newtxpredicts, dbfile, pvals_blocks_to_keep)

        self._calc_pval_ecdf()
        self._calc_pdistance()

    @classmethod
    def from_db(cls, block_halflife, condition_fn=None, dbfile=pvals_dbfile):
        '''Load past tx p-vals from db.

        Only uses the txs for which condition_fn(txpredict) is True.
        '''
        pred = cls(block_halflife)
        heights = pred._get_heights(dbfile=dbfile)
        if not heights:
            return pred
        for height in heights:
            txpredicts = pred._read_block(height, dbfile=dbfile)
            if txpredicts is None:
                continue
            if condition_fn is not None:
                txpredicts = filter(condition_fn, txpredicts)
            pred._add_pvals(txpredicts)
        pred._calc_pval_ecdf()
        pred._calc_pdistance()
        return pred

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
            pvalcount_idx = max(int(ceil(tx.pval*NUM_PVAL_POINTS)) - 1, 0)
            new_pvalcounts[pvalcount_idx] += 1
        for idx in range(len(self.pvalcounts)):
            self.pvalcounts[idx] = (self._alpha*self.pvalcounts[idx] +
                                    (1 - self._alpha)*new_pvalcounts[idx])

    def _calc_pval_ecdf(self):
        totalcount = sum(self.pvalcounts)
        if not totalcount:
            self.pval_ecdf = None
            return
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
        if not self.pval_ecdf:
            self.pdistance = None
            return
        d = [abs(pr - (idx+1)/NUM_PVAL_POINTS)
             for idx, pr in enumerate(self.pval_ecdf)]
        self.pdistance = max(d)

    def _write_block(self, blockheight, txpredicts, dbfile, blocks_to_keep):
        '''Write the block's p-val statistics to db.'''
        db = None
        try:
            db = sqlite3.connect(dbfile)
            for key, val in PVALS_DB_SCHEMA.items():
                db.execute('CREATE TABLE IF NOT EXISTS %s (%s)' %
                           (key, ','.join(val)))
            db.execute('CREATE INDEX IF NOT EXISTS heightidx '
                       'ON txs (blockheight)')
            with db:
                logger.debug("Writing {} predicts to block {}.".format(len(txpredicts), blockheight))
                db.executemany(
                    'INSERT INTO txs VALUES (?,?,?,?,?,?)',
                    [(blockheight,) + tx._get_attr_tuple()
                     for tx in txpredicts])

            if blocks_to_keep > 0:
                height_thresh = blockheight - blocks_to_keep
                with db:
                    db.execute('DELETE FROM txs WHERE blockheight<=?',
                               (height_thresh,))
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _read_block(blockheight, dbfile=pvals_dbfile):
        db = None
        try:
            db = sqlite3.connect(dbfile)
            db.row_factory = Prediction._db_row_factory
            txpredicts = db.execute(
                "SELECT * FROM txs WHERE blockheight=?",
                (blockheight, )).fetchall()
            return txpredicts
        except sqlite3.OperationalError as e:
            if e.message.startswith('no such table'):
                return None
            else:
                raise e
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _get_heights(dbfile=pvals_dbfile):
        '''Get the block heights in the db.

        Returns the list of heights for which tx p-value records exist.
        '''
        db = None
        try:
            db = sqlite3.connect(dbfile)
            heights = db.execute(
                "SELECT DISTINCT blockheight FROM txs").fetchall()
            return sorted([r[0] for r in heights])
        except sqlite3.OperationalError as e:
            if e.message.startswith('no such table'):
                return None
            else:
                raise e
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _db_row_factory(cursor, row):
        '''Row factory for forming TxPrediction object from p-val DB row.'''
        return TxPrediction._from_attr_tuple(row[1:])


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
