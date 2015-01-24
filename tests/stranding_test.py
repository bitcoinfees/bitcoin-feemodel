import unittest
from feemodel.txmempool import MemBlock
from feemodel.stranding import tx_preprocess, calc_stranding_feerate

dbfile = 'data/test.db'


class StrandingTests(unittest.TestCase):
    def setUp(self):
        self.memblock = MemBlock.read(333931, dbfile=dbfile)

    def test_regular(self):
        txs = tx_preprocess(self.memblock)
        stat = calc_stranding_feerate(txs)
        self.assertEqual(stat['sfr'], 23310)
        self.assertEqual(stat['abovekn'], (490, 493))
        self.assertEqual(stat['belowkn'], (282, 285))

    def test_empty(self):
        self.memblock.entries = {}
        txs = tx_preprocess(self.memblock)
        self.assertRaises(ValueError, calc_stranding_feerate, txs)

    def test_all_inblock(self):
        self.memblock.entries = {
            txid: entry for txid, entry in self.memblock.entries.iteritems()
            if entry['inblock']}
        txs = tx_preprocess(self.memblock)
        stat = calc_stranding_feerate(txs)
        print("All in block: %s" % stat)
        self.assertEqual(stat['sfr'], 13940)
        self.assertEqual(stat['abovekn'], (493, 493))
        self.assertEqual(stat['belowkn'], (0, 0))

    def test_zero_inblock(self):
        self.memblock.entries = {
            txid: entry for txid, entry in self.memblock.entries.iteritems()
            if not entry['inblock']}
        txs = tx_preprocess(self.memblock)
        stat = calc_stranding_feerate(txs)
        print("Zero inblock: %s" % stat)
        self.assertEqual(stat['sfr'], float('inf'))
        self.assertEqual(stat['abovekn'], (0, 0))
        self.assertEqual(stat['belowkn'], (312, 312))




if __name__ == '__main__':
    unittest.main()
