import unittest
from feemodel.tests.pseudoproxy import proxy

AVAILABLE_HEIGHTS = range(333931, 333954) + [334655, 334656]


class PseudoProxyTests(unittest.TestCase):

    def test_A(self):
        # Just test that no KeyError is raised: we have the blocks
        # in AVAILABLE_HEIGHTS
        for height in AVAILABLE_HEIGHTS:
            blockhash = proxy.getblockhash(height)
            block = proxy.getblock(blockhash)
            self.assertTrue(block)


if __name__ == "__main__":
    unittest.main()
