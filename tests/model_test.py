import unittest
import threading
from feemodel.model import Model
from feemodel.nonparam import NonParam
import feemodel.txmempool as txmempool
from testconfig import dbFile

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.model = Model()
        self.np = NonParam()
        
        self.model.pushBlocks.register(self.np.pushBlocks)

    def test_noConcurrent(self):
        block = txmempool.Block.blockFromHistory(333931, dbFile=dbFile)
        block2 = txmempool.Block.blockFromHistory(333932, dbFile=dbFile)

        t = threading.Thread(target=self.model.pushBlocks, args=([block],))
        t.start()
        # self.np.pushBlocks raises an AssertionError if there is concurrent access
        self.model.pushBlocks([block2])
        self.assertEqual(len(self.np.blockEstimates), 2)
        t.join()


if __name__ == '__main__':
    unittest.main()
