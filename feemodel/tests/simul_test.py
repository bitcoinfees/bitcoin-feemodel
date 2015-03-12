import unittest
from collections import Counter
from copy import copy, deepcopy

from feemodel.txmempool import get_mempool
from feemodel.simul import SimPool, SimPools, Simul, SimTx, SimTxSource
from feemodel.simul.txsources import SimEntry
from feemodel.simul.pools import SimBlock

init_pools = {
    'pool0': SimPool(0.2, 500000, 20000),
    'pool1': SimPool(0.3, 750000, 10000),
    'pool2': SimPool(0.5, 1000000, 1000)
}

simtxsample = [
    SimTx(640, 11000),
    SimTx(250, 40000),
    SimTx(500, 2000)]
txrate = 1.1
avgtxbyterate = sum([
    tx.size for tx in simtxsample])/float(len(simtxsample))*txrate
blockrate = 1./600

pools = SimPools(pools=init_pools)
tx_source = SimTxSource(simtxsample, txrate)

init_mempool = [SimEntry.from_mementry(txid, entry)
                for txid, entry in get_mempool().items()]
print("Mempool size is %d" %
      sum([entry.tx.size for entry in init_mempool]))


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
        byterates_target = [sum(byterates_binned[idx:])
                            for idx in range(len(byterates_binned))]
        for actual, target in zip(self.tx_byterates, byterates_target):
            self.assertAlmostEqual(actual, target)

    def test_generate(self):
        t = 10000.
        entry_gen = self.tx_source.generate_txs(t)
        tx_gen = [entry.tx for entry in entry_gen]
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
            mempoolsize = sum([entry.tx.size
                               for entry in self.sim.mempool.entries])
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))

        self.sim.cap.print_cap()

    def test_mempool(self):
        for entry in self.init_mempool:
            # tx.depends = []
            entry.tx.feerate = 100000
            # entry.tx.size = 10000
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock, t in self.sim.run(init_entries=self.init_mempool):
            if simblock.height >= 50:
                break
            mempoolsize = sum([entry.tx.size
                               for entry in self.sim.mempool.entries])
            self.assertEqual(simblock.size,
                             sum([tx.size for tx in simblock.txs]))
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


class CustomMempoolTests(unittest.TestCase):
    def setUp(self):
        pools = FakePools()
        self.tx_source = copy(tx_source)
        self.tx_source.txrate = 0.
        self.sim = Simul(pools, self.tx_source)

    def test_A(self):
        init_mempool = [SimEntry(str(i), SimTx(250, 100000), ['0'])
                        for i in range(1, 1000)]
        init_mempool.append(SimEntry('0', SimTx(1000000, 100000)))
        for simblock, t in self.sim.run(init_entries=init_mempool):
            print('MBS: %d, MFR: %d' %
                  (simblock.poolinfo[1].maxblocksize,
                  simblock.poolinfo[1].minfeerate))
            self.assertEqual(len(simblock.txs), 1)
            self.assertEqual(simblock.sfr, 100000)
            self.assertEqual(len(self.sim.mempool.entries), 999)
            break

    def test_B(self):
        init_mempool = [SimEntry(str(i), SimTx(250, 100000), ['0'])
                        for i in range(1, 1000)]
        init_mempool.append(SimEntry('0', SimTx(250, 999)))
        for simblock, t in self.sim.run(init_entries=init_mempool):
            print('MBS: %d, MFR: %d' %
                  (simblock.poolinfo[1].maxblocksize,
                  simblock.poolinfo[1].minfeerate))
            self.assertEqual(len(simblock.txs), 0)
            self.assertEqual(simblock.sfr, 1000)
            self.assertEqual(len(self.sim.mempool.entries), 1000)
            break

    def test_C(self):
        init_mempool = [SimEntry(str(i), SimTx(250, 100000), ['0'])
                        for i in range(1, 1000)]
        init_mempool.append(SimEntry('0', SimTx(900000, 1000)))
        for simblock, t in self.sim.run(init_entries=init_mempool):
            print('MBS: %d, MFR: %d' %
                  (simblock.poolinfo[1].maxblocksize,
                  simblock.poolinfo[1].minfeerate))
            self.assertEqual(len(simblock.txs), 401)
            self.assertEqual(simblock.sfr, 1000)
            self.assertEqual(len(self.sim.mempool.entries), 599)
            break



class FakePools(SimPools):
    def __init__(self):
        super(FakePools, self).__init__(pools=init_pools)

    def blockgen(self):
        def blockgenfn():
            simtime = 0.
            blockheight = 0
            numpools = len(self._SimPools__pools)
            while True:
                poolinfo = self._SimPools__pools[blockheight % numpools]
                blockinterval = 600
                simtime += blockinterval
                simblock = SimBlock(blockheight, simtime,
                                    blockinterval, poolinfo)
                blockheight += 1
                yield simblock
        return blockgenfn()


if __name__ == '__main__':
    unittest.main()
