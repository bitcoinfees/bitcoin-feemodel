from __future__ import division

import os
import logging
import sqlite3
from bisect import bisect
from math import ceil

from tabulate import tabulate

from feemodel.util import Function
from feemodel.config import datadir

logger = logging.getLogger(__name__)

# Should not include 0. Equi-spacing not necessary.
WAIT_PERCENTILE_PTS = [0.05*i for i in range(1, 21)]
NUM_PVAL_POINTS = 20
WAIT_MEDIAN_IDX = bisect(WAIT_PERCENTILE_PTS, 0.5) - 1

PVALS_DB_SCHEMA = {
    'txs': [
        'blockheight INTEGER',
        'txid TEXT',
        'feerate INTEGER',
        'entrytime INTEGER',
        'median_wait INTEGER',
        'waittime INTEGER',
        'pval REAL'
    ]
}
PVALS_DBFILE = os.path.join(datadir, 'pvals.db')
DEFAULT_BLOCKS_TO_KEEP = 2016


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
        self.median_waittime = waitpercentiles[WAIT_MEDIAN_IDX]
        self.feerate = feerate
        self.entrytime = entrytime
        self.waittime = None
        self.pval = None

    def calc_pval(self, blocktime):
        '''Calculate the p-value of the tx.

        blocktime is the timestamp of the block that included the tx.
        '''
        self.waittime = blocktime - self.entrytime
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

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class PValECDF(Function):
    '''p-value empirical CDF.

    Also calculate the predict-distance. This is the Kolmogorov-Smirnov
    distance between the p-value empirical CDF and the uniform
    distribution.

    We're not doing a KS test however, since the transactions have
    dependence and we don't know how to account for that.

    But it's still a useful metric for gauging the model validity.
    A pdistance of d implies that on average, when using the model
    to give a prediction bound W of level L on the wait time w of a tx,
    the probability P that the tx will be within the bound (i.e. w <= W)
    satisfies abs(P - L) <= d.
    '''

    def __init__(self, pvalcounts):
        self.totalcount = sum(pvalcounts)
        if not self.totalcount:
            raise ValueError("No p-values.")
        x = []
        y = []
        d = []
        cumsum = 0
        for idx, count in enumerate(pvalcounts):
            cumsum += count
            p = cumsum / self.totalcount
            y.append(p)
            p_ref = (idx+1) / len(pvalcounts)
            x.append(p_ref)
            d.append(abs(p - p_ref))
        self.pdistance = max(d)
        super(PValECDF, self).__init__(x, y)

    def __str__(self):
        table = [("p-distance", self.pdistance),
                 ("totalcount", self.totalcount)]
        tablestr = tabulate(table)
        return super(PValECDF, self).__str__() + '\n' + tablestr

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        raise NotImplementedError


class Prediction(object):

    def __init__(self, block_halflife, blocks_to_keep=DEFAULT_BLOCKS_TO_KEEP):
        self.block_halflife = block_halflife
        self.blocks_to_keep = blocks_to_keep
        self._alpha = 0.5**(1 / block_halflife)
        self.pvalcounts = [0.]*NUM_PVAL_POINTS
        self.pval_ecdf = None
        self.predicts = {}

    def update_predictions(self, state, transientstats):
        if not transientstats:
            return
        newpredicts = {}
        for txid, entry in state.entries.iteritems():
            if txid in self.predicts:
                continue
            if not entry.depends and not entry.is_high_priority():
                newpredicts[txid] = transientstats.predict(entry.feerate,
                                                           state.time)
            else:
                newpredicts[txid] = None
        self.predicts.update(newpredicts)
        num_new_predicts = len(filter(bool, newpredicts.values()))
        logger.debug("{} new predicts.".format(num_new_predicts))

    def process_blocks(self, blocks, dbfile=PVALS_DBFILE):
        for block in blocks:
            if block is None:
                continue
            predicts_inblock = [
                (txid, predict) for txid, predict in self.predicts.iteritems()
                if predict is not None and
                txid in block.entries and
                block.entries[txid].inblock]
            for txid, predict in predicts_inblock:
                predict.calc_pval(block.time)
            pvals = [predict.pval for txid, predict in predicts_inblock]
            self._add_block_pvals(pvals)
            logger.info("Block {}: {} predicts tallied.".
                        format(block.blockheight, len(predicts_inblock)))

            # Remove from predictions those entries that are no longer
            # in the mempool. This can happen, for example, if the predicts
            # are outdated, or if a tx was removed as a conflict.
            self.predicts = {
                txid: predict for txid, predict in self.predicts.iteritems()
                if txid in block.entries and not block.entries[txid].inblock}
            if dbfile:
                self._write_block(block.blockheight, predicts_inblock,
                                  dbfile, self.blocks_to_keep)

        try:
            self._calc_pval_ecdf()
        except ValueError:
            pass

    def _add_block_pvals(self, pvals):
        new_pvalcounts = [0.]*NUM_PVAL_POINTS
        for pval in pvals:
            pvalcount_idx = max(int(ceil(pval*NUM_PVAL_POINTS)) - 1, 0)
            new_pvalcounts[pvalcount_idx] += 1
        for idx in range(len(self.pvalcounts)):
            self.pvalcounts[idx] = (
                self._alpha*self.pvalcounts[idx] + new_pvalcounts[idx])

    def _calc_pval_ecdf(self):
        '''Calculate the new p-value ECDF.'''
        self.pval_ecdf = PValECDF(self.pvalcounts)

    @classmethod
    def from_db(cls, block_halflife, conditions=None, dbfile=PVALS_DBFILE):
        '''Load past tx p-vals from db.

        Only uses the txs for which condition_fn(txpredict) is True.
        '''
        pred = cls(block_halflife)
        heights = pred._get_heights(dbfile=dbfile)
        if not heights:
            return pred
        for height in heights:
            pvals = pred._read_block(height, conditions=conditions,
                                     dbfile=dbfile)
            pred._add_block_pvals(pvals)
        try:
            pred._calc_pval_ecdf()
        except ValueError:
            pass
        return pred

    def _write_block(self, blockheight, txpredicts, dbfile, blocks_to_keep):
        '''Write the block's txpredicts to db.'''
        db = None
        try:
            db = sqlite3.connect(dbfile)
            for key, val in PVALS_DB_SCHEMA.items():
                db.execute('CREATE TABLE IF NOT EXISTS %s (%s)' %
                           (key, ','.join(val)))
            db.execute('CREATE INDEX IF NOT EXISTS heightidx '
                       'ON txs (blockheight)')
            with db:
                db.executemany(
                    'INSERT INTO txs VALUES (?,?,?,?,?,?,?)',
                    [(blockheight, txid) + predict._get_attr_tuple()
                     for txid, predict in txpredicts])

            if blocks_to_keep > 0:
                height_thresh = blockheight - blocks_to_keep
                with db:
                    db.execute('DELETE FROM txs WHERE blockheight<=?',
                               (height_thresh,))
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _read_block(blockheight, conditions=None, dbfile=PVALS_DBFILE):
        '''Read the p-vals of a block.

        conditions is an SQL WHERE clause expression. Warning: there are no
        protections against SQL injection here.
        '''
        if not os.path.exists(dbfile):
            return []
        db = None
        try:
            db = sqlite3.connect(dbfile)
            query = "SELECT pval FROM txs WHERE blockheight=?"
            if conditions:
                query += " AND ({})".format(conditions)
            pvals = db.execute(query, (blockheight, )).fetchall()
            return [r[0] for r in pvals]
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _get_heights(dbfile=PVALS_DBFILE):
        '''Get the block heights in the db.

        Returns the list of heights for which tx p-value records exist.
        '''
        if not os.path.exists(dbfile):
            return []
        db = None
        try:
            db = sqlite3.connect(dbfile)
            heights = db.execute(
                "SELECT DISTINCT blockheight FROM txs").fetchall()
            return sorted([r[0] for r in heights])
        finally:
            if db is not None:
                db.close()

    # TODO: Deprecate this.
    def print_predicts(self):
        '''Print the pval ECDF and predict-distance.'''
        raise NotImplementedError
        if not self.pval_ecdf:
            raise ValueError("No valid ECDF.")
        headers = ['x', 'F(x)']
        table = zip(
            [i / NUM_PVAL_POINTS for i in range(1, NUM_PVAL_POINTS+1)],
            self.pval_ecdf)
        print("ECDF of p-values")
        print("================")
        print(tabulate(table, headers=headers))
        print("Halflife: {} blocks.".format(self.block_halflife))
        print("Predict-distance: {}".format(self.pdistance))

    def get_stats(self):
        stats = {
            "params": {
                "block_halflife": self.block_halflife,
                "blocks_to_keep": self.blocks_to_keep
            }
        }
        pval_ecdf = self.pval_ecdf
        if pval_ecdf:
            stats.update({
                "pval_ecdf": zip(*pval_ecdf),
                "pdistance": pval_ecdf.pdistance,
                "numtxs": pval_ecdf.totalcount
            })
        return stats

    def __str__(self):
        pval_ecdf = self.pval_ecdf
        if not pval_ecdf:
            return "No valid ECDF."
        table = [("Halflife", self.block_halflife)]
        return str(pval_ecdf) + '\n' + tabulate(table)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__
