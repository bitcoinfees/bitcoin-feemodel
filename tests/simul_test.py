import unittest
from pprint import pprint
from collections import Counter
from copy import copy

from feemodel.util import proxy
from feemodel.txmempool import MemEntry
from feemodel.simul import Pool, Pools, Simul, SimTx, TxSource
from feemodel.simul.simul import steadystate, transient

init_pools = {
    'pool0': Pool(0.2, 500000, 20000),
    'pool1': Pool(0.3, 750000, 10000),
    'pool2': Pool(0.5, 1000000, 1000)
}

txsample = [
    SimTx(640, 11000),
    SimTx(250, 40000),
    SimTx(500, 2000)]
txrate = 1.1
avgtxbyterate = sum([tx.size for tx in txsample])/float(len(txsample))*txrate
blockrate = 1./600

pools = Pools(init_pools=init_pools)
tx_source = TxSource(txsample, txrate)

rawmempool = proxy.getrawmempool(verbose=True)
print("Mempool size is %d" %
      sum([entry['size'] for entry in rawmempool.values()]))


class PoolSimTests(unittest.TestCase):
    def setUp(self):
        self.pools = pools

    def test_basic(self):
        self.pools.print_pools()
        pprint(self.pools.getall())

    def test_randompool(self):
        numiters = 10000
        mbs = []
        for i in range(numiters):
            name, maxblocksize, minfeerate = self.pools.next_block()
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


class BasicSimTest(unittest.TestCase):
    def setUp(self):
        self.tx_source = copy(tx_source)
        self.tx_source.txrate = 1.1
        self.sim = Simul(pools, self.tx_source)
        self.entries = {txid: MemEntry(rawentry)
                        for txid, rawentry in rawmempool.items()}

    def test_basic(self):
        print("basic test...")
        print("sfr\tblksize\tmemsize")
        self.sim.run(self.my_memcb, maxiters=50)

    def test_initmempool(self):
        print("init mempool test...")
        print("sfr\tblksize\tmemsize")
        for entry in self.entries.values():
            entry.depends = []
            entry.feerate = 100000
            entry.size = 10000
        self.sim.run(self.my_initmemcb, mempool=self.entries, maxiters=50)
        self.sim.cap.print_caps()

    def my_memcb(self, sim):
        numbytes, dum = sim.mempool._calc_size()
        memtxs = sim.mempool.get_txs()
        blocktxsize = sum([tx.size for tx in sim.lastblock['txs']])
        self.assertEqual(blocktxsize, sim.lastblock['blocksize'])
        print("%d\t%d\t%d" % (sim.lastblock['sfr'],
                              sim.lastblock['blocksize'], numbytes))

    def my_initmemcb(self, sim):
        self.my_memcb(sim)
        for tx in sim.mempool._tx_havedeps.values():
            self.assertTrue(tx.depends)

        for tx in sim.mempool._tx_nodeps:
            self.assertLessEqual(tx.feerate, sim.lastblock['sfr'])


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




if __name__ == '__main__':
    unittest.main()
