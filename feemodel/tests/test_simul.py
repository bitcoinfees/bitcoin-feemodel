from __future__ import division

import unittest
from collections import Counter
from copy import deepcopy
from random import seed

from feemodel.txmempool import MemBlock
from feemodel.simul import (SimPool, SimPools, Simul, SimTx, SimTxSource,
                            SimEntry)
from feemodel.simul.txsources import TxPtrArray
from feemodel.simul.pools import SimBlock
from feemodel.tests.config import memblock_dbfile as dbfile

seed(0)

ref_pools = {
    'pool0': SimPool(0.2, 500000, 20000),
    'pool1': SimPool(0.3, 750000, 10000),
    'pool2': SimPool(0.5, 1000000, 1000)
}

ref_txsample = [
    SimTx(11000, 640),
    SimTx(40000, 250),
    SimTx(2000, 500)]
ref_txrate = 1.1
ref_mean_byterate = sum([
    tx.size for tx in ref_txsample])/float(len(ref_txsample))*ref_txrate

# b = MemBlock.read(333931, dbfile=dbfile)
init_entries = MemBlock.read(333931, dbfile=dbfile).entries
# init_mempool = [SimEntry.from_mementry(txid, entry)
#                 for txid, entry in b.entries.items()]
print("Mempool size is %d" %
      sum([entry.size for entry in init_entries.values()]))


class PoolSimTests(unittest.TestCase):

    def setUp(self):
        self.simpools = SimPools(pools=ref_pools)

    def test_basic(self):
        self.simpools.print_pools()
        pools = self.simpools.get_pools()
        # Make sure the pools returned by get() is a copy
        for pool in pools.values():
            pool.hashrate = 10000
        self.assertNotEqual(self.simpools.get_pools(), pools)
        print(pools)

    def test_randompool(self):
        numiters = 10000
        poolnames = []
        for simblock, blockinterval in self.simpools.get_blockgen():
            if simblock.height >= numiters:
                break
            poolnames.append(simblock.poolname)

        c = Counter(poolnames)
        for name, pool in self.simpools.get_pools().items():
            count = float(c[name])
            diff = abs(pool.proportion - count/numiters)
            self.assertLess(diff, 0.01)


class TxSourceTests(unittest.TestCase):

    def setUp(self):
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.feerates = [0, 2000, 10999, 20000]
        byterates_binned = [
            0, 500*ref_txrate/3., 640*ref_txrate/3., 250*ref_txrate/3.]
        self.ref_byterates = [sum(byterates_binned[idx:])
                              for idx in range(len(byterates_binned))]

    def test_print_rates(self):
        self.tx_source.print_rates()

    def test_get_byterates(self):
        _dum, byterates = self.tx_source.get_byterates(self.feerates)
        for test, target in zip(byterates, self.ref_byterates):
            self.assertAlmostEqual(test, target)

    def test_emitter(self):
        t = 10000.
        emitted = TxPtrArray()
        tx_emitter = self.tx_source.get_emit_fn(feeratethresh=2000)
        # Emit txs over an interval of t seconds.
        tx_emitter(emitted, t)

        # Compare the tx rate.
        txrate = len(emitted) / t
        diff = abs(txrate - ref_txrate)
        self.assertLess(diff, 0.01)

        # Check that byterates match.
        simtxs = emitted.get_simtxs()
        derivedsource = SimTxSource(simtxs, txrate)
        _dum, byterates = derivedsource.get_byterates(self.feerates)
        for test, target in zip(byterates, self.ref_byterates):
            diff = abs(test - target)
            self.assertLess(diff, 10)

    def test_feerate_threshold(self):
        t = 10000.
        emitted = TxPtrArray()
        tx_emitter = self.tx_source.get_emit_fn(feeratethresh=2001)
        # Emit txs over an interval of t seconds.
        tx_emitter(emitted, t)

        # Compare the tx rate.
        txrate = len(emitted) / t
        # We filtered out 1 out of 3 SimTxs by using feeratethresh = 2001
        ref_txrate_mod = ref_txrate * 2 / 3
        diff = abs(txrate - ref_txrate_mod)
        self.assertLess(diff, 0.01)

        # Check that byterates match.
        simtxs = emitted.get_simtxs()
        derivedsource = SimTxSource(simtxs, txrate)
        _dum, byterates = derivedsource.get_byterates(self.feerates)
        # print("Derived byterates are {}.".format(byterates))
        for idx, ratetuple in enumerate(zip(byterates, self.ref_byterates)):
            test, target = ratetuple
            if idx > 1:
                diff = abs(test - target)
                self.assertLess(diff, 10)

        # Test inf thresh
        emitted = TxPtrArray()
        with self.assertRaises(ValueError):
            tx_emitter = self.tx_source.get_emit_fn(feeratethresh=float("inf"))

    def test_zero_interval(self):
        emitted = TxPtrArray()
        tx_emitter = self.tx_source.get_emit_fn(feeratethresh=2001)
        tx_emitter(emitted, 0)
        self.assertEqual(len(emitted), 0)

    def test_zero_txrate(self):
        self.tx_source = SimTxSource(ref_txsample, 0)
        emitted = TxPtrArray()
        tx_emitter = self.tx_source.get_emit_fn()
        tx_emitter(emitted, 600)
        self.assertEqual(len(emitted), 0)
        # TODO: fix the display when printing with zero txrate
        self.tx_source.print_rates()

    def test_empty_txsample(self):
        self.tx_source = SimTxSource([], ref_txrate)
        with self.assertRaises(ValueError):
            self.tx_source.get_emit_fn()
        with self.assertRaises(ValueError):
            self.tx_source.get_byterates()
        with self.assertRaises(ValueError):
            self.tx_source.calc_mean_byterate()


class BasicSimTest(unittest.TestCase):

    def setUp(self):
        self.simpools = SimPools(pools=ref_pools)
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.sim = Simul(self.simpools, self.tx_source)
        self.init_entries = deepcopy(init_entries)

    def test_basic(self):
        print("Basic Sim: the stable feerate is %d." % self.sim.stablefeerate)
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock in self.sim.run():
            if simblock.height >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))

        self.sim.cap.print_cap()

    def test_mempool(self):
        for entry in self.init_entries.values():
            entry.feerate = 100000
            entry.size = 9927
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for simblock in self.sim.run(init_entries=self.init_entries):
            if simblock.height >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            self.assertEqual(simblock.size,
                             sum([tx.size for tx in simblock.txs]))
            print("%d\t%d\t%d\t%.0f\t%d" %
                  (simblock.height, len(simblock.txs),
                   simblock.size, simblock.sfr, mempoolsize))
        self.sim.cap.print_cap()

    def test_degenerate_pools(self):
        pass
        # self.ref_pools = {'pool0': SimPool(1, 0, float("inf")),
        #                   'pool1': SimPool(1, 0, 0)}
        # # TODO: fix outdated stablefeerate calcs
        # # Raises ValueError because not enough capacity.
        # # self.assertRaises(ValueError, Simul, SimPools(self.ref_pools),
        # #                   self.tx_source)
        # self.ref_pools.update({'pool2': SimPool(3, 1000000, 1000)})
        # self.sim = Simul(SimPools(self.ref_pools), self.tx_source)
        # print("Degenerate pools:")
        # print("Height\tNumtxs\tSize\tSFR")
        # for simblock in self.sim.run():
        #     if simblock.height >= 50:
        #         break
        #     print("%d\t%d\t%d\t%.0f" % (simblock.height, len(simblock.txs),
        #                                 simblock.size, simblock.sfr))
        # self.sim.cap.print_cap()


class CustomMempoolTests(unittest.TestCase):
    # TODO: needs more detailed tests

    def setUp(self):
        pools = PseudoPools()
        tx_source = SimTxSource(ref_txsample, 0)
        self.sim = Simul(pools, tx_source)

    def test_A(self):
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(100000, 1000000)
        for simblock in self.sim.run(init_entries=init_entries):
            print('MBS: %d, MFR: %d' % (simblock.pool.maxblocksize,
                                        simblock.pool.minfeerate))
            self.assertEqual(len(simblock.txs), 1)
            self.assertEqual(simblock.sfr, 100001)
            self.assertEqual(len(self.sim.mempool.get_entries()), 999)
            break

    def test_B(self):
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(999, 250)
        for simblock in self.sim.run(init_entries=init_entries):
            print('MBS: %d, MFR: %d' % (simblock.pool.maxblocksize,
                                        simblock.pool.minfeerate))
            self.assertEqual(len(simblock.txs), 0)
            self.assertEqual(simblock.sfr, 1000)
            self.assertEqual(len(self.sim.mempool.get_entries()), 1000)
            break

    def test_C(self):
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(1000, 900000)
        for simblock in self.sim.run(init_entries=init_entries):
            print('MBS: %d, MFR: %d' % (simblock.pool.maxblocksize,
                                        simblock.pool.minfeerate))
            self.assertEqual(len(simblock.txs), 401)
            self.assertEqual(simblock.sfr, 1001)
            self.assertEqual(len(self.sim.mempool.get_entries()), 599)
            break

    def test_D(self):
        # Chain of txs
        init_entries = {
            str(i): SimEntry(10500-i, 2000, depends=[str(i+1)])
            for i in range(1000)
        }
        # init_mempool = [SimEntry(str(i), SimTx(10500-i, 2000), [str(i+1)])
        #                 for i in range(1000)]
        with self.assertRaises(AssertionError):
            # Hanging dependency
            for simblock in self.sim.run(init_entries=init_entries):
                break

        init_entries['1000'] = SimEntry(1001, 2000)
        # init_mempool.append(SimEntry('1000', SimTx(1001, 2000)))
        for simblock in self.sim.run(init_entries=init_entries):
            if simblock.height == 0:
                self.assertEqual(simblock.sfr, 1002)
                self.assertEqual(max([tx.feerate for tx in simblock.txs]),
                                 9999)
                self.assertEqual(len(simblock.txs), 500)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501)
            elif simblock.height == 1:
                self.assertEqual(simblock.sfr, 10001)
                self.assertEqual(len(simblock.txs), 375)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501-375)
                self.assertEqual(simblock.size, 750000)
            elif simblock.height == 2:
                self.assertEqual(simblock.sfr, 20000)
                self.assertEqual(len(simblock.txs), 0)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501-375)
                self.assertEqual(simblock.size, 0)
            elif simblock.height == 3:
                self.assertEqual(simblock.sfr, 1000)
                self.assertEqual(len(simblock.txs), 501-375)
                self.assertEqual(len(self.sim.mempool.get_entries()), 0)
                self.assertEqual(simblock.size, 2000*(501-375))
            else:
                break


class PseudoPools(SimPools):
    """SimPools with deterministic blockgen."""

    def __init__(self):
        super(PseudoPools, self).__init__(pools=ref_pools)

    def get_blockgen(self):
        def blockgenfn():
            simtime = 0.
            blockheight = 0
            numpools = len(self._SimPools__pools)
            while True:
                poolname, pool = self._SimPools__pools[blockheight % numpools]
                blockinterval = 600
                simtime += blockinterval
                simblock = SimBlock(blockheight, simtime, poolname, pool)
                blockheight += 1
                yield simblock, blockinterval
        return blockgenfn()


if __name__ == '__main__':
    unittest.main()
