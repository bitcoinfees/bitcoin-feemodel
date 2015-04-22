'''Test app.predict.'''
from __future__ import division

import unittest
from math import exp
from random import random, expovariate

from feemodel.tests.config import (transientstatsref as transientstats,
                                   tmpdatadir_context)
from feemodel.app.predict import (Prediction, DEFAULT_BLOCKS_TO_KEEP,
                                  NUM_PVAL_POINTS)
from feemodel.txmempool import MemBlock

HALFLIFE = 1000


class PredictTests(unittest.TestCase):

    def setUp(self):
        self.pred = Prediction(HALFLIFE)
        with tmpdatadir_context():
            self.b = MemBlock.read(333931)
            extra_entries = MemBlock.read(333932)
        self.pred.update_predictions(self.b, transientstats)
        self.pred.update_predictions(extra_entries, transientstats)

    def test_A(self):
        pred = self.pred
        blocktime = self.b.time
        b = self.b

        for txpredict in pred.predicts.values():
            # Adjust the entry time so that the p-value will be
            # uniform in [0, 1]
            if txpredict is None:
                continue
            target_pval = random()
            waittime = txpredict.inv(target_pval)
            txpredict.entrytime = blocktime - waittime

        pred.process_blocks([b], dbfile=None)
        pvalcount = sum(pred.pvalcounts)
        pdistance = pred.pval_ecdf.pdistance
        print("p-distance is {}.".format(pdistance))
        for idx, p in enumerate(pred.pval_ecdf):
            diff = abs(p[1] - (idx+1)/len(pred.pval_ecdf))
            self.assertLessEqual(diff, pdistance)
        self.assertLess(pdistance, 0.1)

        # Check the cleanup of pred.predicts
        for txid, entry in b.entries.items():
            if entry.inblock:
                self.assertNotIn(txid, pred.predicts)
            else:
                self.assertIn(txid, pred.predicts)
        for txid in pred.predicts:
            self.assertIn(txid, b.entries)

        # Check the exponential decay
        N = 10
        for i in range(N):
            pred.process_blocks([b], dbfile=None)

        newpdistance = pred.pval_ecdf.pdistance
        self.assertAlmostEqual(newpdistance, pdistance)
        newpvalcount = sum(pred.pvalcounts)
        self.assertAlmostEqual(newpvalcount, pvalcount*0.5**(N/HALFLIFE))

    def test_B(self):
        # 0 wait time
        pred = self.pred
        blocktime = self.b.time
        b = self.b
        for txpredict in pred.predicts.values():
            if txpredict:
                txpredict.entrytime = blocktime
        pred.process_blocks([b], dbfile=None)
        pdistance = pred.pval_ecdf.pdistance
        print("p-distance is {}.".format(pdistance))
        binsize = 1 / NUM_PVAL_POINTS
        self.assertEqual(pdistance, 1-binsize)
        self.assertEqual(pred.pval_ecdf(1-binsize), 0)

    def test_C(self):
        # inf wait time
        pred = self.pred
        b = self.b
        for txpredict in pred.predicts.values():
            if txpredict:
                txpredict.entrytime = -float("inf")
        pred.process_blocks([b], dbfile=None)
        pdistance = pred.pval_ecdf.pdistance
        binsize = 1 / NUM_PVAL_POINTS
        print("p-distance is {}.".format(pdistance))
        self.assertEqual(pdistance, 1-binsize)
        self.assertEqual(pred.pval_ecdf(binsize), 1)

    def test_D(self):
        # empty block entries
        pred = self.pred
        b = self.b
        b.entries = {}
        pred.process_blocks([b], dbfile=None)

    def test_E(self):
        # DB checks
        with tmpdatadir_context():
            pred = self.pred
            b = self.b
            blocktime = b.time
            for txpredict in pred.predicts.values():
                if txpredict:
                    txpredict.entrytime = blocktime - 600
            pred_db = Prediction.from_db(HALFLIFE)
            self.assertIsNone(pred_db.pval_ecdf)
            pred.process_blocks([b])
            pred_db = Prediction.from_db(HALFLIFE, conditions="waittime>600")
            # No pvals
            self.assertRaises(ValueError, pred_db.print_predicts)

            b.blockheight += DEFAULT_BLOCKS_TO_KEEP - 1
            pred.update_predictions(b, transientstats)
            for txpredict in pred.predicts.values():
                if txpredict:
                    txpredict.entrytime = blocktime - 300
            pred.process_blocks([b])

            # Check stat is unchanged on load
            pred_db = Prediction.from_db(HALFLIFE)
            for p, p_db in zip(pred.pval_ecdf, pred_db.pval_ecdf):
                self.assertAlmostEqual(p[1], p_db[1])
            self.assertEqual(pred_db.pval_ecdf.pdistance,
                             pred.pval_ecdf.pdistance)

            # Check the circular db deletes
            heights = pred._get_heights()
            self.assertEqual(
                heights, [333931, 333931+DEFAULT_BLOCKS_TO_KEEP-1])

            b.blockheight += 1
            pred.update_predictions(b, transientstats)
            for txpredict in pred.predicts.values():
                if txpredict:
                    txpredict.entrytime = blocktime - 100
            pred.process_blocks([b])

            heights = pred._get_heights()
            self.assertEqual(
                heights,
                [333931+DEFAULT_BLOCKS_TO_KEEP-1,
                 333931+DEFAULT_BLOCKS_TO_KEEP])
            pred_db = Prediction.from_db(HALFLIFE)
            self.assertTrue(any([
                abs(p[1]-p_db[1]) >= 0.00001
                for p, p_db in zip(pred.pval_ecdf, pred_db.pval_ecdf)]))
            self.assertNotAlmostEqual(pred.pval_ecdf.pdistance,
                                      pred_db.pval_ecdf.pdistance)


class GeneralTests(unittest.TestCase):

    def test_A(self):
        # Test Prediction._add_block_pvals
        pred = Prediction(HALFLIFE)
        for i in range(1000):
            p = [expovariate_pval(expovariate(1)) for i in xrange(300)]
            pred._add_block_pvals(p)
        pred._calc_pval_ecdf()
        print("pdistance is {}.".format(pred.pval_ecdf.pdistance))
        self.assertLess(pred.pval_ecdf.pdistance, 0.01)


def expovariate_pval(r):
    '''Returns the pvalue of an expovariate with rate 1.'''
    return exp(-r)


if __name__ == '__main__':
    unittest.main()
