import unittest
from collections import Counter
from copy import copy, deepcopy
from bisect import bisect

from feemodel.util import proxy, Table
from feemodel.txmempool import MemEntry, get_mempool
from feemodel.simul import SimPool, SimPools, Simul, SimTx, SimTxSource
from feemodel.simul.txsources import TxSourceCopy

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
        for name, pool in self.pools.get().items():
            count = float(c[name])
            diff = abs(pool.proportion - count/numiters)
            self.assertLess(diff, 0.01)


class TxSourceTests(unittest.TestCase):
    def setUp(self):
        self.tx_source = tx_source
        self.feerates = [0, 2000, 10999, 20000]
        self.tx_byterates = self.tx_source.get_byterates(self.feerates)

    def test_basic(self):
        byterates_binned = [0, 500*txrate/3., 640*txrate/3., 250*txrate/3.]
        byterates_target = [sum(byterates_binned[idx:]) for idx in range(len(byterates_binned))]
        for actual, target in zip(self.tx_byterates, byterates_target):
            self.assertAlmostEqual(actual, target)

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
        print("Basic Sim: the stable feerate is %d." % self.sim.stablefeerate)

    def test_basic(self):
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock, t in self.sim.run():
            if simblock.height >= 50:
                break
            mempoolsize = sum([tx.size for tx in self.sim.mempool.txs])
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))

        self.sim.cap.print_cap()

    def test_mempool(self):
        for tx in self.init_mempool:
            tx.depends = []
            tx.feerate = 100000
            tx.size = 10000
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock, t in self.sim.run(mempooltxs=self.init_mempool):
            if simblock.height >= 50:
                break
            mempoolsize = sum([tx.size for tx in self.sim.mempool.txs])
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))
        self.sim.cap.print_cap()

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
        for simblock, t in self.sim.run():
            if simblock.height >= 50:
                break
            print("%d\t%d\t%d\t%.0f" % (simblock.height, len(simblock.txs),
                                        simblock.size, simblock.sfr))
        self.sim.cap.print_cap()


# #class SimCapsTest(unittest.TestCase):
# #    def setUp(self):
# #        self.tx_source = copy(tx_source)
# #        self.tx_source.txrate = 3.
# #        self.pools = pools
# #        self.sim = Simul(self.pools, self.tx_source)
# #
# #    def test_A(self):
# #        cap = self.sim.cap
# #        pool_empcaps = {name: PoolEmpiricalCap(poolcap)
# #                        for name, poolcap in cap.pool_caps.items()}
# #        for simblock, t in self.sim.run(maxtime=600., maxiters=100000):
# #            for poolec in pool_empcaps.values():
# #                poolec.totaltime += simblock.interval
# #            pool_empcaps[simblock.poolinfo[0]].addblock(simblock)
# #        print("Completed in %.2fs with %d iters." % (t, simblock.height+1))
# #        print("Stable feerate is %d" % self.sim.stablefeerate)
# #        cap.print_cap()
# #        for name, poolec in pool_empcaps.items():
# #            print("%s:\n=================" % name)
# #            poolec.calc_simproc()
# #            poolec.print_procs()
# #
# #
# #class PoolEmpiricalCap(object):
# #    def __init__(self, poolcap):
# #        self.feerates = poolcap.feerates
# #        self.procrates_theory = poolcap.procrates
# #        self.procrates_sim = [0.]*len(self.feerates)
# #        self.caps = poolcap.caps
# #        self.totaltime = 0.
# #
# #    def addblock(self, simblock):
# #        for tx in simblock.txs:
# #            fidx = bisect(self.feerates, tx.feerate)
# #            self.procrates_sim[fidx-1] += tx.size
# #
# #    def calc_simproc(self):
# #        self.procrates_sim = [r / self.totaltime for r in self.procrates_sim]
# #
# #    def print_procs(self):
# #        table = Table()
# #        table.add_row(("Feerate", "Theory", "Sim", "Caps"))
# #        for idx in range(len(self.feerates)):
# #            table.add_row((
# #                self.feerates[idx],
# #                '%.2f' % self.procrates_theory[idx],
# #                '%.2f' % self.procrates_sim[idx],
# #                '%.2f' % self.caps[idx],
# #            ))
# #        table.print_table()

# #class SteadyStateTest(unittest.TestCase):
# #    def test_steadystate(self):
# #        self.tx_source = copy(tx_source)
# #        self.tx_source.txrate = 1.1
# #        stats = steadystate(pools, self.tx_source, maxtime=10)
# #        stats.print_stats()
# #
# #
# #class TransientTest(unittest.TestCase):
# #    def setUp(self):
# #        self.tx_source = copy(tx_source)
# #        self.tx_source.txrate = 1.1
# #        self.init_mempool = deepcopy(init_mempool)
# #
# #    def test_normal(self):
# #        print("Normal mempool")
# #        stats = transient(self.init_mempool, pools, self.tx_source, maxtime=10)
# #        stats.print_stats()
# #
# #    def test_no_mp(self):
# #        print("No mempool")
# #        stats = transient([], pools, self.tx_source, maxtime=10)
# #        stats.print_stats()
# #
# #    def test_aug_mp(self):
# #        print("Augmented mempool")
# #        for simtx in self.init_mempool:
# #            simtx.depends = []
# #            simtx.feerate = 100000
# #            simtx.size = 10000
# #        stats = transient(self.init_mempool, pools, self.tx_source, maxtime=10)
# #        stats.print_stats()
# #
# #    def test_stopflag(self):
# #        print("Stop test with normal mempool")
# #        stats = transient(self.init_mempool, pools, self.tx_source, maxiters=500)
# #        stats.print_stats()


if __name__ == '__main__':
    unittest.main()
