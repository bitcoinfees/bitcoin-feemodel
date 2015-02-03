import unittest
from collections import Counter
from copy import copy, deepcopy
from bisect import bisect

from feemodel.util import proxy, Table
from feemodel.txmempool import MemEntry, get_mempool
from feemodel.simul import SimPool, SimPools, Simul, SimTx, SimTxSource
from feemodel.simul.txsources import TxSourceCopy
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
tx_source_copy = TxSourceCopy(txsample, txrate)

init_mempool = [SimTx.from_mementry(txid, entry)
                for txid, entry in get_mempool().items()]
print("Mempool size is %d" %
      sum([tx.size for tx in init_mempool]))


class PoolSimTests(unittest.TestCase):
    def setUp(self):
        self.pools = pools

    def test_basic(self):
        self.pools.print_pools()
        pools = self.pools.get()
        # Make sure the pools returned by get() is a copy
        for pool in pools.values():
            pool.hashrate = 10000
        self.assertNotEqual(self.pools.get(), pools)
        print(pools)

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
        self.init_mempool = deepcopy(init_mempool)

    def test_basic(self):
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock, t in self.sim.run(maxiters=50):
            mempoolsize = sum([tx.size for tx in self.sim.mempool.txs])
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))

        self.sim.cap.print_caps()

    def test_mempool(self):
        for tx in self.init_mempool:
            tx.depends = []
            tx.feerate = 100000
            tx.size = 10000
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock, t in self.sim.run(mempool=self.init_mempool,
                                        maxiters=50):
            mempoolsize = sum([tx.size for tx in self.sim.mempool.txs])
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))
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


class SimCapsTest(unittest.TestCase):
    def setUp(self):
        self.tx_source = copy(tx_source)
        self.tx_source.txrate = 3.
        self.pools = pools
        self.sim = Simul(self.pools, self.tx_source)

    def test_A(self):
        cap = self.sim.cap
        pool_empcaps = {name: PoolEmpiricalCap(poolcap)
                        for name, poolcap in cap.pool_caps.items()}
        for simblock, t in self.sim.run(maxtime=60.):
            for poolec in pool_empcaps.values():
                poolec.totaltime += simblock.interval
            pool_empcaps[simblock.poolinfo[0]].addblock(simblock)
        print("Completed in %.2fs with %d iters." % (t, simblock.height+1))
        cap.print_caps()
        for name, poolec in pool_empcaps.items():
            print("%s:\n=================" % name)
            poolec.calc_simproc()
            poolec.print_procs()


class PoolEmpiricalCap(object):
    def __init__(self, poolcap):
        self.feerates = poolcap.feerates
        self.procrates_theory = poolcap.procrates
        self.procrates_sim = [0.]*len(self.feerates)
        self.caps = poolcap.caps
        self.totaltime = 0.

    def addblock(self, simblock):
        for tx in simblock.txs:
            fidx = bisect(self.feerates, tx.feerate)
            self.procrates_sim[fidx-1] += tx.size

    def calc_simproc(self):
        self.procrates_sim = [r / self.totaltime for r in self.procrates_sim]

    def print_procs(self):
        table = Table()
        table.add_row(("Feerate", "Theory", "Sim", "Caps"))
        for idx in range(len(self.feerates)):
            table.add_row((
                self.feerates[idx],
                '%.2f' % self.procrates_theory[idx],
                '%.2f' % self.procrates_sim[idx],
                '%.2f' % self.caps[idx],
            ))
        table.print_table()


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
        self.init_mempool = deepcopy(init_mempool)

    def test_normal(self):
        print("Normal mempool")
        stats = transient(self.init_mempool, pools, self.tx_source, maxtime=10)
        stats.print_stats()

    def test_no_mp(self):
        print("No mempool")
        stats = transient([], pools, self.tx_source, maxtime=10)
        stats.print_stats()

    def test_aug_mp(self):
        print("Augmented mempool")
        for simtx in self.init_mempool:
            simtx.depends = []
            simtx.feerate = 100000
            simtx.size = 10000
        stats = transient(self.init_mempool, pools, self.tx_source, maxtime=10)
        stats.print_stats()

    def test_stopflag(self):
        print("Stop test with normal mempool")
        stats = transient(self.init_mempool, pools, self.tx_source, maxiters=500)
        stats.print_stats()


if __name__ == '__main__':
    unittest.main()
