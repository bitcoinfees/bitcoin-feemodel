from __future__ import division

import unittest
import threading
import multiprocessing
from time import time
from random import seed, expovariate
from math import log
from pprint import pprint
from collections import Counter
from copy import deepcopy, copy

from feemodel.txmempool import MemBlock
from feemodel.simul import (SimPool, SimPools, Simul, SimTx, SimTxSource,
                            SimEntry)
from feemodel.simul.pools import SimBlock
from feemodel.tests.config import test_memblock_dbfile as dbfile
from feemodel.simul.simul import SimMempool
from feemodel.simul.transient import transientsim_core, transientsim
from feemodel.util import cumsum_gen
from feemodel.tests.config import txref

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

init_entries = MemBlock.read(333931, dbfile=dbfile).entries
print("Mempool size is %d" %
      sum([entry.size for entry in init_entries.values()]))


class PoolSimTests(unittest.TestCase):

    def test_basic(self):
        simpools = SimPools(ref_pools)
        print(simpools)
        print(simpools.get_capacityfn())
        print(simpools.get_hashratefn())

    def test_blockgen(self):
        """Test the convergence of the random gen."""
        seed(1)
        ref_blockrate = 1/400
        simpools = SimPools(ref_pools, blockrate=ref_blockrate)
        numiters = 10000
        poolnames = []
        totaltime = 0
        for idx, (simblock, blockinterval) in enumerate(
                simpools.blockgen()):
            if idx >= numiters:
                break
            totaltime += blockinterval
            poolnames.append(simblock.poolname)

        # Test the relative frequencies of pools
        c = Counter(poolnames)
        totalhashrate = simpools.calc_totalhashrate()
        for name, pool in simpools.pools.items():
            count = float(c[name])
            expected_relfreq = pool.hashrate / totalhashrate
            diff = abs(log(expected_relfreq) - log(count/numiters))
            self.assertLess(diff, 0.01)

        # Test the sample mean of the block intervals
        blockinterval_samplemean = totaltime / numiters
        diff = abs(log(blockinterval_samplemean * ref_blockrate))
        self.assertLess(diff, 0.01)

    def test_caps(self):
        simpools = SimPools(ref_pools)
        ref_feerates = (999, 1000, 10000, 20000)
        ref_caps = tuple(cumsum_gen(
            [0, 0.5*1000000/600, 0.3*750000/600, 0.2*500000/600]))
        feerates, caps = zip(*simpools.get_capacityfn())
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)
        ref_hashrates = (0, 0.5, 0.8, 1)
        feerates, hashrates = zip(*simpools.get_hashratefn())
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(hashrates, ref_hashrates)

        # Duplicate minfeerate
        newref_pools = deepcopy(ref_pools)
        newref_pools.update({'pool3': SimPool(0.1, 600000, 1000)})
        newref_pools['pool1'].hashrate = 0.2
        simpools = SimPools(newref_pools)
        feerates, caps = zip(*simpools.get_capacityfn())
        ref_feerates = (999, 1000, 10000, 20000)
        ref_caps = tuple(cumsum_gen(
            [0, 0.5*1000000/600 + 0.1*600000/600,
             0.2*750000/600, 0.2*500000/600]))
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)
        ref_hashrates = (0, 0.6, 0.8, 1)
        feerates, hashrates = zip(*simpools.get_hashratefn())
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(hashrates, ref_hashrates)

        # Inf minfeerate
        newref_pools = deepcopy(ref_pools)
        newref_pools['pool0'].minfeerate = float("inf")
        simpools = SimPools(newref_pools)
        feerates, caps = zip(*simpools.get_capacityfn())
        ref_feerates = (999, 1000, 10000)
        ref_caps = tuple(cumsum_gen(
            [0, 0.5*1000000/600, 0.3*750000/600]))
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)
        ref_hashrates = (0, 0.5, 0.8)
        feerates, hashrates = zip(*simpools.get_hashratefn())
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(hashrates, ref_hashrates)

        # Only inf minfeerate
        newref_pools = deepcopy(ref_pools)
        for pool in newref_pools.values():
            pool.minfeerate = float("inf")
        simpools = SimPools(newref_pools)
        with self.assertRaises(ValueError):
            feerates, caps = zip(*simpools.get_capacityfn())
        with self.assertRaises(ValueError):
            feerates, caps = zip(*simpools.get_hashratefn())

        # Empty pools
        simpools = SimPools({})
        with self.assertRaises(ValueError):
            simpools.get_capacityfn()
        with self.assertRaises(ValueError):
            simpools.get_hashratefn()


class TxSourceTests(unittest.TestCase):

    def setUp(self):
        seed(1)
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.feerates = [0, 2000, 10999, 20000]
        byterates_binned = [
            0, 500*ref_txrate/3., 640*ref_txrate/3., 250*ref_txrate/3.]
        self.ref_byterates = list(cumsum_gen(reversed(byterates_binned)))
        self.ref_byterates.reverse()

    def test_print_rates(self):
        print(self.tx_source)
        print(self.tx_source.get_byteratefn())

    def test_get_byterates(self):
        print("Ref byterates:")
        for feerate, byterate in zip(self.feerates, self.ref_byterates):
            print("{}\t{}".format(feerate, byterate))
        byteratefn = self.tx_source.get_byteratefn()
        for feerate, refrate in zip(self.feerates, self.ref_byterates):
            self.assertAlmostEqual(refrate, byteratefn(feerate))

    def test_emitter(self):
        # Test that the long-run average byterates of emitted txs
        # are close to the expected values.
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool, feeratethresh=2000)

        t = 0
        maxtime = 10000.
        while t < maxtime:
            # Use a mean interval of 10 min
            interval = expovariate(1/600)
            tx_emitter(interval)
            t += interval
        simtxs = mempool.get_entries().values()

        # Compare the tx rate.
        txrate = len(simtxs) / t
        diff = abs(log(txrate) - log(ref_txrate))
        self.assertLess(diff, 0.02)

        # Check that byterates match.
        derivedsource = SimTxSource(simtxs, txrate)
        byteratefn = derivedsource.get_byteratefn()
        for feerate, refrate in zip(self.feerates, self.ref_byterates):
            diff = abs(log(byteratefn(feerate)) - log(refrate))
            self.assertLess(diff, 0.02)

    def test_feerate_threshold(self):
        t = 10000.
        # emitted = TxPtrArray()
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool, feeratethresh=2001)
        # Emit txs over an interval of t seconds.
        tx_emitter(t)
        simtxs = mempool.get_entries().values()

        # Compare the tx rate.
        txrate = len(simtxs) / t
        # We filtered out 1 out of 3 SimTxs by using feeratethresh = 2001
        ref_txrate_mod = ref_txrate * 2 / 3
        diff = abs(log(txrate) - log(ref_txrate_mod))
        self.assertLess(diff, 0.01)

        # Check that byterates match.
        derivedsource = SimTxSource(simtxs, txrate)
        byteratefn = derivedsource.get_byteratefn()
        for idx, feerate in enumerate(self.feerates):
            if idx > 1:
                refrate = self.ref_byterates[idx]
                testrate = byteratefn(feerate)
                diff = abs(log(testrate) - log(refrate))
                self.assertLess(diff, 0.01)

        # Test thresh equality
        mempool.reset()
        tx_emitter = self.tx_source.get_emitter(mempool, feeratethresh=2000)
        # Emit txs over an interval of t seconds.
        tx_emitter(t)
        simtxs = mempool.get_entries().values()

        # Compare the tx rate.
        txrate = len(simtxs) / t
        diff = abs(log(txrate) - log(ref_txrate))
        self.assertLess(diff, 0.01)

        # Test inf thresh
        mempool.reset()
        tx_emitter = self.tx_source.get_emitter(mempool,
                                                feeratethresh=float("inf"))
        tx_emitter(t)
        self.assertEqual(len(mempool.get_entries()), 0)

    def test_zero_interval(self):
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool)
        tx_emitter(0)
        self.assertEqual(len(mempool.get_entries()), 0)

    def test_null_source(self):
        mempool = SimMempool({})
        self.tx_source.txrate = 0
        with self.assertRaises(ValueError):
            self.tx_source.get_emitter(mempool)
        self.tx_source.txrate = 1
        self.tx_source.txsample = []
        with self.assertRaises(ValueError):
            self.tx_source.get_emitter(mempool)

    def test_randomness(self):
        # Test that txsource does not generate identical txs when doing
        # multiprocessing.
        numtxs = 10
        txsource = copy(txref)
        # With the standard txref, unique txs is ~ 500 txs
        uniquetxs = set([(tx.feerate, tx.size) for tx in txref.txsample])
        txsource.txsample = [SimTx(*tx) for tx in uniquetxs]

        def target(conn):
            mempool = SimMempool({})
            tx_emitter = txsource.get_emitter(mempool)
            while len(mempool.get_entries()) < numtxs:
                tx_emitter(1)
            txs = [(entry.feerate, entry.size)
                   for entry in mempool.get_entries().values()]
            conn.send(txs)

        parent0, child0 = multiprocessing.Pipe()
        parent1, child1 = multiprocessing.Pipe()
        process0 = multiprocessing.Process(target=target, args=(child0,))
        process1 = multiprocessing.Process(target=target, args=(child1,))
        process0.start()
        process1.start()

        txs0 = set(parent0.recv())
        txs1 = set(parent1.recv())
        intersectsize = len(txs0 & txs1)
        print("len of intersection is {}.".format(intersectsize))
        # Probabilistic test
        self.assertLess(intersectsize, 2)
        process0.join()
        process1.join()


class BasicSimTests(unittest.TestCase):

    def setUp(self):
        self.simpools = SimPools(pools=ref_pools)
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.sim = Simul(self.simpools, self.tx_source)
        self.init_entries = deepcopy(init_entries)

    def test_basic(self):
        print("Basic Sim: the stable feerate is %d." % self.sim.stablefeerate)
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for idx, simblock in enumerate(self.sim.run()):
            if idx >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            print("%d\t%d\t%d\t%.0f\t%d" % (idx, len(simblock.txs),
                                            simblock.size, simblock.sfr,
                                            mempoolsize))

    def test_mempool(self):
        for entry in self.init_entries.values():
            entry.feerate = 100000
            entry.size = 9927
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for idx, simblock in enumerate(
                self.sim.run(init_entries=self.init_entries)):
            if idx >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            self.assertEqual(simblock.size,
                             sum([tx.size for tx in simblock.txs]))
            print("%d\t%d\t%d\t%.0f\t%d" % (idx, len(simblock.txs),
                                            simblock.size, simblock.sfr,
                                            mempoolsize))

    def test_degenerate_pools(self):
        degen_pools = {'pool0': SimPool(1, 0, float("inf")),
                       'pool1': SimPool(1, 0, 0)}
        with self.assertRaises(ValueError):
            # No capacity.
            Simul(SimPools(degen_pools), self.tx_source)

        degen_pools.update({'pool2': SimPool(3, 1000000, 1000)})
        degen_pools['pool0'].maxblocksize = 1
        degen_pools['pool1'].maxblocksize = 1
        self.sim = Simul(SimPools(degen_pools), self.tx_source)

        print("Degenerate pools:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for idx, simblock in enumerate(self.sim.run()):
            if idx >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            print("%d\t%d\t%d\t%.0f\t%d" % (idx, len(simblock.txs),
                                            simblock.size, simblock.sfr,
                                            mempoolsize))

    def test_insane_feerates(self):
        # Test the restriction of feerates to unsigned int.
        for entry in self.init_entries.values():
            entry.feerate = 100000000000000000000000000000000
            entry.size = 9927
        print("Insane feerates:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for idx, simblock in enumerate(
                self.sim.run(init_entries=self.init_entries)):
            if idx >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            self.assertEqual(simblock.size,
                             sum([tx.size for tx in simblock.txs]))
            print("%d\t%d\t%d\t%.0f\t%d" % (idx, len(simblock.txs),
                                            simblock.size, simblock.sfr,
                                            mempoolsize))


class CustomMempoolTests(unittest.TestCase):
    # TODO: needs more detailed tests

    def setUp(self):
        pools = PseudoPools()
        tx_source = SimTxSource([SimTx(0, 250)], 1)
        self.sim = Simul(pools, tx_source)

    def test_A(self):
        print("Test A:")
        print("=======")
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(100000, 1000000)
        for simblock in self.sim.run(init_entries=init_entries):
            print(simblock)
            self.assertEqual(len(simblock.txs), 1)
            self.assertEqual(simblock.sfr, 100001)
            self.assertEqual(len(self.sim.mempool.get_entries()), 999)
            break

    def test_B(self):
        print("Test B:")
        print("=======")
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(999, 250)
        for simblock in self.sim.run(init_entries=init_entries):
            print(simblock)
            self.assertEqual(len(simblock.txs), 0)
            self.assertEqual(simblock.sfr, 1000)
            self.assertEqual(len(self.sim.mempool.get_entries()), 1000)
            break

    def test_C(self):
        print("Test C:")
        print("=======")
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(1000, 900000)
        for simblock in self.sim.run(init_entries=init_entries):
            print(simblock)
            self.assertEqual(len(simblock.txs), 401)
            self.assertEqual(simblock.sfr, 1001)
            self.assertEqual(len(self.sim.mempool.get_entries()), 599)
            break

    def test_D(self):
        print("Test D:")
        print("=======")
        # Chain of txs
        init_entries = {
            str(i): SimEntry(10500-i, 2000, depends=[str(i+1)])
            for i in range(1000)
        }
        with self.assertRaises(ValueError):
            # Hanging dependency
            for simblock in self.sim.run(init_entries=init_entries):
                break

        init_entries['1000'] = SimEntry(1001, 2000)
        # init_mempool.append(SimEntry('1000', SimTx(1001, 2000)))
        for idx, simblock in enumerate(
                self.sim.run(init_entries=init_entries)):
            print(simblock)
            if idx == 0:
                self.assertEqual(simblock.sfr, 1002)
                self.assertEqual(max([tx.feerate for tx in simblock.txs]),
                                 9999)
                self.assertEqual(len(simblock.txs), 500)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501)
            elif idx == 1:
                self.assertEqual(simblock.sfr, 10001)
                self.assertEqual(len(simblock.txs), 375)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501-375)
                self.assertEqual(simblock.size, 750000)
            elif idx == 2:
                self.assertEqual(simblock.sfr, 20000)
                self.assertEqual(len(simblock.txs), 0)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501-375)
                self.assertEqual(simblock.size, 0)
            elif idx == 3:
                self.assertEqual(simblock.sfr, 1000)
                self.assertEqual(len(simblock.txs), 501-375)
                self.assertEqual(len(self.sim.mempool.get_entries()), 0)
                self.assertEqual(simblock.size, 2000*(501-375))
            else:
                break


class TransientSimTests(unittest.TestCase):

    def setUp(self):
        self.simpools = SimPools(pools=ref_pools)
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.sim = Simul(self.simpools, self.tx_source)
        self.init_entries = deepcopy(init_entries)
        self.feepoints = [0, 1000, 5000, 10000, 20000]
        self.feepoints = filter(
            lambda feerate: feerate >= self.sim.stablefeerate, self.feepoints)

    def test_basic(self):
        # Just check that core can run.
        print("Stable feerate is: {}".format(self.sim.stablefeerate))
        print(self.feepoints)
        for idx, waitvector in enumerate(transientsim_core(self.sim,
                                                           self.init_entries,
                                                           self.feepoints)):
            if idx == 10:
                break
            print(waitvector)

        # Check that ValueError is raised if there are feepoints below
        # sim.stablefeerate
        with self.assertRaises(ValueError):
            transientsim_core(self.sim, self.init_entries,
                              [self.sim.stablefeerate-1]).next()

        # No feepoints >= stablefeerate
        with self.assertRaises(ValueError):
            feepoints, waittimes = transientsim(
                self.sim,
                feepoints=[self.sim.stablefeerate-1],
                init_entries=init_entries,
                miniters=0,
                maxiters=1000,
                maxtime=60)

    def test_monoprocess(self):
        NUMPROCESSES = 1

        MAXITERS = 2000
        print("Testing maxiters:")
        feepoints, waittimes = transientsim(
            self.sim,
            feepoints=self.feepoints,
            init_entries=init_entries,
            miniters=0,
            maxiters=MAXITERS,
            maxtime=60,
            numprocesses=NUMPROCESSES)
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))
        numiters = len(waittimes[0])
        self.assertLess(numiters, MAXITERS*1.2)

        print("Testing maxtime:")
        MAXTIME = 1
        starttime = time()
        feepoints, waittimes = transientsim(
            self.sim,
            feepoints=self.feepoints,
            init_entries=init_entries,
            miniters=0,
            maxiters=10000,
            maxtime=MAXTIME,
            numprocesses=NUMPROCESSES)
        timespent = time() - starttime
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))
        self.assertLess(timespent, MAXTIME*1.2)

        print("Testing miniters: ")
        MINITERS = 2000
        feepoints, waittimes = transientsim(
            self.sim,
            feepoints=self.feepoints,
            init_entries=init_entries,
            miniters=MINITERS,
            maxiters=10000,
            maxtime=0,
            numprocesses=NUMPROCESSES)
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))
        numiters = len(waittimes[0])
        self.assertLess(numiters, MINITERS*1.1)

        print("Testing auto feepoints:")
        feepoints, waittimes = transientsim(
            self.sim,
            init_entries=init_entries,
            miniters=0,
            maxiters=10000,
            maxtime=5,
            numprocesses=NUMPROCESSES)
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))

        print("Testing stopflag:")
        stopflag = threading.Event()
        threading.Timer(1, stopflag.set).start()
        with self.assertRaises(StopIteration):
            feepoints, waittimes = transientsim(
                self.sim,
                init_entries=init_entries,
                miniters=0,
                maxiters=1000000000000,
                maxtime=60,
                numprocesses=NUMPROCESSES,
                stopflag=stopflag)

    def test_multiprocess(self):
        MAXITERS = 2000
        print("Testing maxiters:")
        feepoints, waittimes = transientsim(
            self.sim,
            feepoints=self.feepoints,
            init_entries=init_entries,
            miniters=0,
            maxiters=MAXITERS,
            maxtime=60)
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))
        numiters = len(waittimes[0])
        self.assertLess(numiters, MAXITERS*1.2)

        print("Testing maxtime:")
        MAXTIME = 1
        starttime = time()
        feepoints, waittimes = transientsim(
            self.sim,
            feepoints=self.feepoints,
            init_entries=init_entries,
            miniters=0,
            maxiters=10000,
            maxtime=MAXTIME)
        timespent = time() - starttime
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))
        self.assertLess(timespent, MAXTIME*1.2)

        print("Testing miniters: ")
        MINITERS = 2000
        feepoints, waittimes = transientsim(
            self.sim,
            feepoints=self.feepoints,
            init_entries=init_entries,
            miniters=MINITERS,
            maxiters=10000,
            maxtime=0)
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        pprint(zip(feepoints, avgwaittimes))
        numiters = len(waittimes[0])
        self.assertLess(numiters, MINITERS*1.2)


class PseudoPools(SimPools):
    """SimPools with deterministic blockgen."""

    def __init__(self):
        super(PseudoPools, self).__init__(ref_pools)

    def blockgen(self):
        poolitems = sorted(self.pools.items(),
                           key=lambda poolitem: poolitem[1].hashrate,
                           reverse=True)
        numpools = len(poolitems)
        idx = 0
        while True:
            poolname, pool = poolitems[idx % numpools]
            blockinterval = 600
            simblock = SimBlock(poolname, pool)
            yield simblock, blockinterval
            idx += 1


if __name__ == '__main__':
    unittest.main()
