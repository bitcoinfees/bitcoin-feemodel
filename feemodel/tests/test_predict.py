'''Test app.predict.'''
from __future__ import division

import unittest
import logging
import os
from math import exp
from random import random, expovariate
from feemodel.app.predict import Prediction, pvals_blocks_to_keep
from feemodel.txmempool import MemBlock, get_mempool
from feemodel.util import load_obj

logging.basicConfig(level=logging.DEBUG)

dbfile = 'data/test.db'
pvals_dbfile = 'data/_tmp.db'
transientstats = load_obj('data/transientstats_ref.pickle')

HALFLIFE = 1000

if os.path.exists(pvals_dbfile):
    os.remove(pvals_dbfile)


class PredictTests(unittest.TestCase):

    def setUp(self):
        self.pred = Prediction(HALFLIFE)
        self.b = MemBlock.read(333931, dbfile=dbfile)
        self.pred.update_predictions(self.b.entries, transientstats)
        self.pred.update_predictions(get_mempool(), transientstats)

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

        pred.process_block([b], dbfile=pvals_dbfile)
        pvalcount = sum(pred.pvalcounts)
        pdistance = pred.pdistance
        print("p-distance is {}.".format(pdistance))
        for idx, p in enumerate(pred.pval_ecdf):
            diff = abs(p - (idx+1)/len(pred.pval_ecdf))
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
            pred.process_block([b])

        newpdistance = pred.pdistance
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
        pred.process_block([b], dbfile=pvals_dbfile)
        pdistance = pred.pdistance
        print("p-distance is {}.".format(pdistance))
        self.assertEqual(pdistance, 0.99)
        self.assertEqual(pred.pval_ecdf[-2], 0)

    def test_C(self):
        # inf wait time
        pred = self.pred
        b = self.b
        for txpredict in pred.predicts.values():
            if txpredict:
                txpredict.entrytime = -float("inf")
        pred.process_block([b], dbfile=pvals_dbfile)
        pdistance = pred.pdistance
        print("p-distance is {}.".format(pdistance))
        self.assertEqual(pdistance, 0.99)
        self.assertEqual(pred.pval_ecdf[0], 1)

    def test_D(self):
        # empty block entries
        pred = self.pred
        b = self.b
        b.entries = {}
        pred.process_block([b], dbfile=pvals_dbfile)

    def test_E(self):
        # DB checks
        pred = self.pred
        b = self.b
        blocktime = b.time
        for txpredict in pred.predicts.values():
            if txpredict:
                txpredict.entrytime = blocktime - 600
        pred_db = Prediction.from_db(HALFLIFE, dbfile=pvals_dbfile)
        self.assertIsNone(pred_db.pval_ecdf)
        pred.process_block([b], dbfile=pvals_dbfile)
        pred_db = Prediction.from_db(HALFLIFE, conditions="waittime>600",
                                     dbfile=pvals_dbfile)
        # No pvals
        self.assertRaises(ValueError, pred_db.print_predicts)

        b.height += pvals_blocks_to_keep - 1
        pred.update_predictions(b.entries, transientstats)
        for txpredict in pred.predicts.values():
            if txpredict:
                txpredict.entrytime = blocktime - 300
        pred.process_block([b], dbfile=pvals_dbfile)

        # Check stat is unchanged on load
        pred_db = Prediction.from_db(HALFLIFE, dbfile=pvals_dbfile)
        for p, p_db in zip(pred.pval_ecdf, pred_db.pval_ecdf):
            self.assertAlmostEqual(p, p_db)
        self.assertEqual(pred_db.pdistance, pred.pdistance)

        # Check the circular db deletes
        heights = pred._get_heights(dbfile=pvals_dbfile)
        self.assertEqual(heights, [333931, 333931+pvals_blocks_to_keep-1])

        b.height += 1
        pred.update_predictions(b.entries, transientstats)
        for txpredict in pred.predicts.values():
            if txpredict:
                txpredict.entrytime = blocktime - 100
        pred.process_block([b], dbfile=pvals_dbfile)

        heights = pred._get_heights(dbfile=pvals_dbfile)
        self.assertEqual(
            heights,
            [333931+pvals_blocks_to_keep-1, 333931+pvals_blocks_to_keep])
        pred_db = Prediction.from_db(HALFLIFE, dbfile=pvals_dbfile)
        self.assertTrue(any([
            abs(p-p_db) >= 0.00001
            for p, p_db in zip(pred.pval_ecdf, pred_db.pval_ecdf)]))
        self.assertNotAlmostEqual(pred.pdistance, pred_db.pdistance)

    def tearDown(self):
        if os.path.exists(pvals_dbfile):
            os.remove(pvals_dbfile)


class GeneralTests(unittest.TestCase):

    def test_A(self):
        # Test Prediction._add_block_pvals
        pred = Prediction(HALFLIFE)
        for i in range(1000):
            p = [expovariate_pval(expovariate(1)) for i in xrange(300)]
            pred._add_block_pvals(p)
        pred._calc_pval_ecdf()
        print("pdistance is {}.".format(pred.pdistance))
        self.assertLess(pred.pdistance, 0.01)


def expovariate_pval(r):
    '''Returns the pvalue of an expovariate with rate 1.'''
    return exp(-r)


if __name__ == '__main__':
    unittest.main()
