import unittest
from copy import deepcopy
from time import time
from feemodel.txmempool import MemBlock
from feemodel.app.predict import Prediction

feerates = range(0, 100000, 10000)
dbfile = 'data/test.db'


class PredictTest(unittest.TestCase):
    def setUp(self):
        self.predicts = Prediction(feerates, 2016)
        self.transientstats = FakeTransientStats()
        self.memblock = MemBlock.read(333931, dbfile=dbfile)
        self.entries = deepcopy(self.memblock.entries)

    def test_inf(self):
        self.transientstats.waittime = 100000
        self.predicts.update_predictions(self.entries, self.transientstats)
        self.memblock.time = time() + 10
        self.predicts.process_block([self.memblock])
        self.predicts.print_scores()
        scores = self.predicts.scores
        for idx in range(len(scores.feerates)):
            self.assertEqual(scores.num_in[idx], scores.numtxs[idx])

    def test_zero(self):
        self.transientstats.waittime = 1
        self.predicts.update_predictions(self.entries, self.transientstats)
        self.memblock.time = time() + 10
        self.predicts.process_block([self.memblock])
        self.predicts.print_scores()
        scores = self.predicts.scores
        for idx in range(len(scores.feerates)):
            self.assertFalse(scores.num_in[idx])


class FakeTransientStats(object):
    def __init__(self):
        self.waittime = 0.

    def predict(self, feerate):
        return self.waittime


if __name__ == '__main__':
    unittest.main()
