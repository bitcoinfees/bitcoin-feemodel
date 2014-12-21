import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.queue import QEstimator
from testconfig import dbFile, saveBlocksFile
import threading
from random import choice

maxMFR = 200000

class QEstimatorTests(unittest.TestCase):
    # def test_pushBlock(self):
    #     qe = QEstimator(maxMFR,adaptive=2016)
    #     qe.loadBlocks(saveBlocksFile)

    #     qe2 = QEstimator(maxMFR,adaptive=2016)
    #     for height, block in qe.blocks.iteritems():
    #         qe2.pushBlock(height, block[0], block[1])

    #     qe.adaptiveCalc()
    #     qe2.adaptiveCalc()

    #     self.assertTrue(qe == qe2)

    def test_consecutiveReadFromHistory(self):
        qe = QEstimator(maxMFR, adaptive=2016)
        qe.readFromHistory((333931,333953),dbFile=dbFile)

        qe2 = QEstimator(maxMFR, adaptive=2016)
        qe2.readFromHistory((333931, 333949),dbFile=dbFile)
        qe2.readFromHistory((333949, 333953),dbFile=dbFile)

        qe3 = QEstimator(maxMFR, adaptive=2016)
        qe3.readFromHistory((333931, 333949),dbFile=dbFile)
        qe3.readFromHistory((333931, 333953),dbFile=dbFile)

        qe.adaptiveCalc()
        qe2.adaptiveCalc()
        qe3.adaptiveCalc()

        self.assertTrue(qe==qe2)
        self.assertTrue(qe2==qe3)


if __name__ == '__main__':
    unittest.main()

