import unittest
from feemodel.txmempool import MemBlock
from feemodel.tests.pseudoproxy import proxy
from feemodel.tests.config import test_memblock_dbfile as dbfile

AVAILABLE_HEIGHTS = range(333931, 333954) + [334655, 334656]


class PseudoProxyTests(unittest.TestCase):

    def test_A(self):
        # Just test that no KeyError is raised: we have the blocks
        # in AVAILABLE_HEIGHTS
        for height in AVAILABLE_HEIGHTS:
            blockhash = proxy.getblockhash(height)
            block = proxy.getblock(blockhash)
            self.assertTrue(block)

    def test_B(self):
        # Test the setting of rawmempool
        proxy.set_rawmempool(333931)
        rawmempool = proxy.getrawmempool()
        b = MemBlock.read(333931, dbfile=dbfile)
        self.assertEqual(set(b.entries), set(rawmempool))
        for txid, rawentry in rawmempool.items():
            for key, val in rawentry.items():
                self.assertEqual(val, getattr(b.entries[txid], key))


if __name__ == "__main__":
    unittest.main()
