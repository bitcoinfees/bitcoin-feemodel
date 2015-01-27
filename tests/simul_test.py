import unittest
from pprint import pprint
from collections import Counter
from copy import copy
from feemodel.simul import Pool, Pools, Simul, SimTx, TxSource

init_pools = {
    'pool0': Pool(0.2, 500000, 20000),
    'pool1': Pool(0.3, 750000, 10000),
    'pool2': Pool(0.5, 1000000, 1000)
}

txsample = [
    SimTx('', 640, 11000),
    SimTx('', 250, 40000),
    SimTx('', 500, 2000)]
txrate = 1.1
avgtxbyterate = sum([tx.size for tx in txsample])/float(len(txsample))*txrate
blockrate = 1./600

pools = Pools(init_pools=init_pools)
tx_source = TxSource(txsample, txrate)


class PoolSimTests(unittest.TestCase):
    def setUp(self):
        self.pools = pools

    def test_basic(self):
        self.pools.print_pools()
        pprint(self.pools.getpools())

    def test_randompool(self):
        numiters = 10000
        mbs = []
        for i in range(numiters):
            maxblocksize, minfeerate = self.pools.next_block()
            mbs.append(maxblocksize)

        c = Counter(mbs)
        for name, pool in self.pools:
            count = float(c[pool.maxblocksize])
            diff = abs(pool.hashrate - count/numiters)
            self.assertLess(diff, 0.01)

    def test_cap(self):
        for rate in range(1, 4):
            source = copy(tx_source)
            source.txrate = rate
            cap = self.pools.calc_capacities(source, blockrate)
            stablefeerate = cap.calc_stablefeerate(0.9)
            cap.print_caps()
            print("The stable fee rate is %d" % stablefeerate)

        cap = self.pools.calc_capacities(tx_source, blockrate)
        self.assertAlmostEqual(sum(cap.tx_byterates), avgtxbyterate)


class TxSourceTests(unittest.TestCase):
    def setUp(self):
        self.tx_source = tx_source
        self.feerates = [0, 2000, 10999, 20000]
        self.tx_byterates = self.tx_source.get_byterates(self.feerates)

    def test_basic(self):
        target = [0, 500*txrate/3., 640*txrate/3., 250*txrate/3.]
        self.assertEqual(self.tx_byterates, target)

    def test_generate(self):
        t = 10000.
        tx_gen = self.tx_source.generate_txs(t)
        self.txrate = len(tx_gen) / t
        diff = abs(self.txrate - txrate)
        self.assertLess(diff, 0.05)
        source = TxSource(tx_gen, self.txrate)
        tx_byterates = source.get_byterates(self.feerates)
        for idx in range(len(self.tx_byterates)):
            diff = abs(self.tx_byterates[idx] - tx_byterates[idx])
            self.assertLess(diff, 10)


# #class SimpleSimul(unittest.TestCase):
# #    def test_basic(self):
# #        miner = SimpleMiner(1000000, 1000)
# #        tx_source = SimpleTxSource(640, 11000, 1.1)
# #        sim = Simul(miner, tx_source)
# #        sim.steady_state(maxtime=5)



if __name__ == '__main__':
    unittest.main()
