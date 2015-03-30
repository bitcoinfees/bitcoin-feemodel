'''Test app.predict.'''
from __future__ import division

import unittest
import logging
from math import log
from random import random
from feemodel.app.predict import Prediction
from feemodel.txmempool import MemBlock, get_mempool
from feemodel.util import load_obj

logging.basicConfig(level=logging.DEBUG)

dbfile = 'data/test.db'
transientstats = load_obj('data/transientstats_ref.pickle')

HALFLIFE = 1000


class PredictTests(unittest.TestCase):
    def test_A(self):
        pred = Prediction(HALFLIFE)
        b = MemBlock.read(333931, dbfile=dbfile)
        blocktime = b.time
        # inblockentries = {txid: entry for txid, entry in b.entries.items() if entry.inblock}
        pred.update_predictions(b.entries, transientstats)
        pred.update_predictions(get_mempool(), transientstats)

        for txpredict in pred.predicts.values():
            # Adjust the entry time so that the p-value will be uniform in [0, 1]
            if txpredict is None:
                continue
            target_pval = random()
            waittime = txpredict.inv(target_pval)
            txpredict.entrytime = blocktime - waittime

        pred.process_block([b])
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


if __name__ == '__main__':
    unittest.main()
