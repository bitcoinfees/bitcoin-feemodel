import unittest
import decimal
from feemodel.util import proxy

class GetRawMempoolTest(unittest.TestCase):
    def test_getrawmempool(self):
        mapTx = proxy.getrawmempool(verbose=True)
        if not mapTx:
            self.fail("No transactions in mempool!")
        else:
            txid = mapTx.keys()[0]
            self.assertTrue(txid.isalnum())
            entry = mapTx[txid]
            self.assertTrue(isinstance(entry['currentpriority'], decimal.Decimal))
            self.assertTrue(isinstance(entry['startingpriority'], decimal.Decimal))
            self.assertTrue(isinstance(entry['fee'], decimal.Decimal))
            self.assertTrue(all([txid.isalnum() for txid in entry['depends']]))
            self.assertTrue(isinstance(entry['height'], int))
            self.assertTrue(isinstance(entry['size'], int))
            self.assertTrue(isinstance(entry['time'], int))

if __name__ == '__main__':
    unittest.main()
