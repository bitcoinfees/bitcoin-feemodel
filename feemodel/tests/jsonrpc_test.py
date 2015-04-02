import unittest
import decimal
import itertools
from time import time
from feemodel.util import CacheProxy, BlockingProxy, proxy


class RPCTests(unittest.TestCase):
    def test_pollMempool(self):
        blockcount, rawmempool = proxy.poll_mempool()
        if not rawmempool:
            self.fail("No transactions in mempool!")
        else:
            txid = rawmempool.keys()[0]
            self.assertTrue(txid.isalnum())
            entry = rawmempool[txid]
            self.assertTrue(
                isinstance(entry['currentpriority'], decimal.Decimal))
            self.assertTrue(
                isinstance(entry['startingpriority'], decimal.Decimal))
            self.assertTrue(isinstance(entry['fee'], decimal.Decimal))
            self.assertTrue(
                all([_txid.isalnum() for _txid in entry['depends']]))
            self.assertTrue(isinstance(entry['height'], int))
            self.assertTrue(isinstance(entry['size'], int))
            self.assertTrue(isinstance(entry['time'], int))


class CacheProxyTest(unittest.TestCase):

    def setUp(self):
        self.cacheproxy = CacheProxy(maxblocks=10)
        self.proxy = BlockingProxy()

    def test_accuracy(self):
        for idx, height in enumerate(itertools.cycle(range(333931, 333954))):
            if idx == 20:
                break
            cache_hash = self.cacheproxy.getblockhash(height)
            normal_hash = self.proxy.getblockhash(height)
            self.assertEqual(cache_hash, normal_hash)
            cache_block = self.cacheproxy.getblock(cache_hash)
            normal_block = self.proxy.getblock(normal_hash)
            self.assertEqual(cache_block, normal_block)

    def test_speed(self):
        maxiters = 100
        starttime = time()
        for idx, height in enumerate(itertools.cycle(range(333931, 333941))):
            if idx == maxiters:
                break
            self.cacheproxy.getblock(self.cacheproxy.getblockhash(height))
        print("cache proxy took {} seconds.".format(time()-starttime))

        starttime = time()
        for idx, height in enumerate(itertools.cycle(range(333931, 333941))):
            if idx == maxiters:
                break
            self.proxy.getblock(self.cacheproxy.getblockhash(height))
        print("proxy took {} seconds.".format(time()-starttime))

    def test_pop(self):
        for height in range(333931, 333941):
            self.cacheproxy.getblock(self.cacheproxy.getblockhash(height))

        self.cacheproxy.getblock(self.cacheproxy.getblockhash(333931))
        self.assertEqual(
            self.cacheproxy.hashmap.keys(), range(333932, 333941) + [333931])

    def tearDown(self):
        self.cacheproxy.close()
        self.proxy.close()


if __name__ == '__main__':
    unittest.main()
