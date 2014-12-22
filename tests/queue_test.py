import unittest
import os
import feemodel.txmempool as txmempool
import feemodel.nonparam as nonparam
from feemodel.model import ModelError
from feemodel.util import logWrite
from feemodel.queue import QEOnline
from testconfig import dbFile, saveQueueFile

maxMFR = 200000

class QEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.lh = txmempool.LoadHistory(dbFile=dbFile)

    # def test_consecutiveReadFromHistory(self):
    #     qe = QEOnline(maxMFR, adaptive=2016)
    #     qe2 = QEOnline(maxMFR, adaptive=2016)
    #     qe3 = QEOnline(maxMFR, adaptive=2016)

    #     lh.registerFn(lambda x: qe.pushBlocks(x,True), (333931,333954))
    #     lh.registerFn(lambda x: qe.pu)
        

    #     qe2 = QEstimator(maxMFR, adaptive=2016)
    #     qe2.readFromHistory((333931, 333949),dbFile=dbFile)
    #     qe2.readFromHistory((333949, 333953),dbFile=dbFile)
    #     qe2.adaptiveCalc(currHeight=333953)
        

    #     qe3 = QEstimator(maxMFR, adaptive=2016)
    #     qe3.readFromHistory((333931, 333949),dbFile=dbFile)
    #     qe3.readFromHistory((333931, 333953),dbFile=dbFile)
    #     qe3.adaptiveCalc(currHeight=333953)
        
    #     self.assertEqual(qe, qe2)
    #     self.assertEqual(qe2, qe3)
    #     self.assertEqual(len(qe.blocks), 18)

    def test_adaptiveDeletes(self):
        qe = QEOnline(maxMFR, adaptive=10, loadFile=None)
        self.lh.registerFn(lambda x: qe.pushBlocks(x,True), (333931, 333954))
        self.lh.loadBlocks()
        qe.adaptiveCalc()
        self.assertEqual(len(qe.blockData), 11)
        qe.saveBlockData(dbFile=saveQueueFile)
        qe2 = QEOnline(maxMFR, adaptive=10, loadFile=None)
        qe2.loadBlockData(dbFile=saveQueueFile)
        qe2.adaptiveCalc()
        self.assertEqual(qe,qe2)
        os.remove(saveQueueFile)


if __name__ == '__main__':
    unittest.main()

