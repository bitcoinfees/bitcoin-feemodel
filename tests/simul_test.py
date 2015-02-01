import unittest
from collections import Counter
from copy import copy, deepcopy

from feemodel.util import proxy
from feemodel.txmempool import MemEntry
from feemodel.simul import SimPool, SimPools, Simul, SimTx, SimTxSource
from feemodel.simul.stats import steadystate, transient

init_pools = {
    'pool0': SimPool(0.2, 500000, 20000),
    'pool1': SimPool(0.3, 750000, 10000),
    'pool2': SimPool(0.5, 1000000, 1000)
}

txsample = [
    SimTx(640, 11000),
    SimTx(250, 40000),
    SimTx(500, 2000)]
txrate = 1.1
avgtxbyterate = sum([tx.size for tx in txsample])/float(len(txsample))*txrate
blockrate = 1./600

pools = SimPools(pools=init_pools)
tx_source = SimTxSource(txsample, txrate)

rawmempool = proxy.getrawmempool(verbose=True)
entries = {txid: MemEntry(rawentry)
                for txid, rawentry in rawmempool.items()}
print("Mempool size is %d" %
      sum([entry['size'] for entry in rawmempool.values()]))


class PoolSimTests(unittest.TestCase):
    def setUp(self):
        self.pools = pools

    def test_basic(self):
        self.pools.print_pools()

    def test_randompool(self):
        numiters = 10000
        poolnames = []
        for idx, simblock in enumerate(self.pools.blockgen()):
            if idx >= numiters:
                break
            poolnames.append(simblock.poolinfo[0])

        c = Counter(poolnames)
        for name, pool in init_pools.items():
            count = float(c[name])
            diff = abs(pool.hashrate - count/numiters)
            self.assertLess(diff, 0.01)

    def test_cap(self):
        for rate in range(1, 4):
            source = copy(tx_source)
            source.txrate = rate
            cap = self.pools.calc_capacities(source)
            stablefeerate = cap.calc_stablefeerate(0.9)
            cap.print_caps()
            print("The stable fee rate is %d" % stablefeerate)

        cap = self.pools.calc_capacities(tx_source)
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
        source = SimTxSource(tx_gen, self.txrate)
        tx_byterates = source.get_byterates(self.feerates)
        for idx in range(len(self.tx_byterates)):
            diff = abs(self.tx_byterates[idx] - tx_byterates[idx])
            self.assertLess(diff, 10)


class BasicSimTest(unittest.TestCase):
    def setUp(self):
        self.tx_source = copy(tx_source)
        self.pools = pools
        self.sim = Simul(self.pools, self.tx_source)
        self.entries = deepcopy(entries)

    def test_basic(self):
        print("Height\tNumtxs\tSize\tSFR")
        for simblock, t in self.sim.run(maxiters=50):
            print("%d\t%d\t%d\t%.0f" % (simblock.height, len(simblock.txs),
                                        simblock.size, simblock.sfr))
        self.sim.cap.print_caps()

    def test_mempool(self):
        for entry in self.entries.values():
            entry.depends = []
            entry.feerate = 100000
            entry.size = 10000
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR")
        for simblock, t in self.sim.run(mempool=self.entries, maxiters=50):
            print("%d\t%d\t%d\t%.0f" % (simblock.height, len(simblock.txs),
                                        simblock.size, simblock.sfr))
        self.sim.cap.print_caps()

    def test_degenerate_pools(self):
        self.init_pools = {'pool0': SimPool(1, 0, float("inf")),
                           'pool1': SimPool(1, 0, 0)}
        # Raises ValueError because not enough capacity.
        self.assertRaises(ValueError, Simul, SimPools(self.init_pools),
                          self.tx_source)
        self.init_pools.update({'pool2': SimPool(3, 1000000, 1000)})
        self.sim = Simul(SimPools(self.init_pools), self.tx_source)
        print("Degenerate pools:")
        print("Height\tNumtxs\tSize\tSFR")
        for simblock, t in self.sim.run(maxiters=50):
            print("%d\t%d\t%d\t%.0f" % (simblock.height, len(simblock.txs),
                                        simblock.size, simblock.sfr))
        self.sim.cap.print_caps()


class SteadyStateTest(unittest.TestCase):
    def test_steadystate(self):
        self.tx_source = copy(tx_source)
        self.tx_source.txrate = 1.1
        stats = steadystate(pools, self.tx_source, maxtime=10)
        stats.print_stats()


class TransientTest(unittest.TestCase):
    def setUp(self):
        self.tx_source = copy(tx_source)
        self.tx_source.txrate = 1.1
        self.entries = {txid: MemEntry(rawentry)
                        for txid, rawentry in rawmempool.items()}

    def test_normal(self):
        print("Normal mempool")
        stats = transient(self.entries, pools, self.tx_source, maxtime=10)
        stats.print_stats()

    def test_no_mp(self):
        print("No mempool")
        stats = transient({}, pools, self.tx_source, maxtime=10)
        stats.print_stats()

    def test_aug_mp(self):
        print("Augmented mempool")
        for entry in self.entries.values():
            entry.depends = []
            entry.feerate = 100000
        stats = transient(self.entries, pools, self.tx_source, maxtime=10)
        stats.print_stats()

    def test_stopflag(self):
        print("Stop test with normal mempool")
        stats = transient(self.entries, pools, self.tx_source, maxiters=500)
        stats.print_stats()


if __name__ == '__main__':
    unittest.main()
