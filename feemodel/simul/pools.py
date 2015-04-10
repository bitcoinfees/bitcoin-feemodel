from __future__ import division

from random import random, expovariate
from math import log, exp
from copy import deepcopy
from bisect import bisect_left
from feemodel.util import Table
from feemodel.simul.simul import BlockTxs

default_blockrate = 1./600
hard_maxblocksize = 1000000


class SimBlock(object):
    def __init__(self, poolname, pool):
        self.poolname = poolname
        self.pool = pool
        self.size = 0
        self.sfr = float("inf")
        self.is_sizeltd = None
        self.txs = []

    @property
    def txs(self):
        '''Get the block transactions as a SimTx list.

        For efficiency, we keep the txs as a BlockTxs (as assigned in
        SimMempool._process_block), and only instantiate the SimTxs
        the first time you access it.

        Take note that if the Simul instance that produced this SimBlock
        becomes unreferenced, the memory to which BlockTxs points will
        become deallocated, and bad things will happen.

        TL;DR - if you want to access this property, make sure you maintain
        a reference to the Simul instance.
        '''
        if isinstance(self._txs, BlockTxs):
            self._txs = self._txs.get_simtxs()
        return self._txs

    @txs.setter
    def txs(self, val):
        self._txs = val

    def __repr__(self):
        return "SimBlock(pool: {}, numtxs: {}, size: {}, sfr: {})".format(
            self.poolname, len(self._txs), self.size, self.sfr)


class SimPool(object):
    def __init__(self, hashrate, maxblocksize, minfeerate):
        self.hashrate = hashrate
        self.maxblocksize = maxblocksize
        self.minfeerate = minfeerate
        self.proportion = None

    def __cmp__(self, other):
        return cmp(self.hashrate, other.hashrate)

    def __repr__(self):
        return ("SimPool{hashrate: %.2f, maxblocksize: %d, minfeerate: %.0f}" %
                (self.hashrate, self.maxblocksize, self.minfeerate))

    def __eq__(self, other):
        relevant_attrs = [
            'hashrate',
            'maxblocksize',
            'minfeerate',
            'proportion']
        return all([
            getattr(self, attr) == getattr(other, attr)
            for attr in relevant_attrs])


class SimPools(object):
    def __init__(self, pools=None, blockrate=default_blockrate):
        self.blockrate = blockrate
        self.totalhashrate = None
        self.__pools = []
        self.__poolsidx = []

        if pools:
            self.update(pools)

    def get_blockgen(self):
        if not self:
            raise ValueError("No pools.")

        def blockgenfn():
            while True:
                poolidx = bisect_left(self.__poolsidx, random())
                poolname, pool = self.__pools[poolidx]
                blockinterval = expovariate(self.blockrate)
                simblock = SimBlock(poolname, pool)
                yield simblock, blockinterval

        return blockgenfn()

    def get_pools(self):
        return {name: deepcopy(pool) for name, pool in self.__pools}

    def update(self, pools):
        poolitems = sorted(
            [(name, deepcopy(pool)) for name, pool in pools.items()],
            key=lambda p: p[1], reverse=True)
        totalhashrate = sum([pool.hashrate for name, pool in poolitems])
        if not totalhashrate:
            raise ValueError("No pools.")

        self.__poolsidx = []
        self.__pools = []
        cumprop = 0.
        try:
            for name, pool in poolitems:
                for attr in ['maxblocksize', 'minfeerate']:
                    if getattr(pool, attr) < 0:
                        raise ValueError("%s must be >= 0." % attr)
                if pool.hashrate <= 0:
                    raise ValueError("hashrate must be > 0.")
                pool.proportion = pool.hashrate / totalhashrate
                cumprop += pool.proportion
                self.__poolsidx.append(cumprop)
                self.__pools.append((name, pool))
            assert abs(cumprop-1) < 0.0001
        except ValueError as e:
            self.__poolsidx = []
            self.__pools = []
            raise(e)

        self.__poolsidx[-1] = 1.
        self.totalhashrate = totalhashrate

    def get_capacity(self):
        poolfeerates = [pool.minfeerate for name, pool in self.__pools
                        if pool.minfeerate < float("inf")]
        poolfeerates = sorted(set(poolfeerates + [0]))

        cap_lower = [
            sum([pool.proportion*pool.maxblocksize
                 for name, pool in self.__pools
                 if pool.minfeerate <= feerate])*self.blockrate
            for feerate in poolfeerates]

        cap_upper = [
            sum([pool.proportion*hard_maxblocksize
                 for name, pool in self.__pools
                 if pool.minfeerate <= feerate])*self.blockrate
            for feerate in poolfeerates]

        return poolfeerates, cap_lower, cap_upper

    def clear_pools(self):
        self.__pools = []
        self.__poolsidx = []

    def print_pools(self):
        table = Table()
        table.add_row(("Name", "Prop", "MBS", "MFR"))
        for name, pool in self.__pools:
            table.add_row((
                name,
                '%.2f' % pool.proportion,
                pool.maxblocksize,
                pool.minfeerate))
        table.print_table()
        print("Avg block interval is %.2f" % (1./self.blockrate,))

    def __nonzero__(self):
        return bool(len(self.__pools))

    def __repr__(self):
        elogp = -sum([p.proportion*log(p.proportion)
                     for n, p in self.__pools])
        numeffpools = exp(elogp)
        return "SimPools(Num: %d, NumEffective: %.2f)" % (
            len(self.__pools), numeffpools)

    def __eq__(self, other):
        return self.__pools == other.__pools

    # #def calc_capacities(self, tx_source):
    # #    poolfeerates = [pool.minfeerate for name, pool in self.__pools]
    # #    poolfeerates = sorted(set(poolfeerates + [0]))
    # #    poolfeerates = filter(lambda fee: fee < float("inf"), poolfeerates)
    # #    tx_byterates = tx_source.get_byterates(poolfeerates)
    # #    pool_caps = {
    # #        name: PoolCapacity(poolfeerates, self.blockrate, pool)
    # #        for name, pool in self.__pools}
    # #    for idx in range(len(poolfeerates)-1, -1, -1):
    # #        excessrate = tx_byterates[idx]
    # #        while excessrate > 0:
    # #            nonmaxedpools = filter(
    # #                lambda pool: pool.procrates[idx] < pool.caps[idx],
    # #                pool_caps.values())
    # #            if not nonmaxedpools:
    # #                break
    # #            totalprop = sum([
    # #                pool.proportion for pool in nonmaxedpools])
    # #            for pool in nonmaxedpools:
    # #                ratealloc = pool.proportion * excessrate / totalprop
    # #                pool.procrates[idx] += ratealloc
    # #                pool.procrates[idx] = min(pool.procrates[idx],
    # #                                          pool.caps[idx])
    # #            excessrate = tx_byterates[idx] - sum([
    # #                pool.procrates[idx] for pool in pool_caps.values()])
    # #        for pool in pool_caps.values():
    # #            pool.update_capacities()

    # #    return Capacity(poolfeerates, tx_byterates, pool_caps)


# #class Capacity(object):
# #    def __init__(self, feerates, tx_byterates, pool_caps):
# #        self.feerates = feerates
# #        self.tx_byterates = tx_byterates
# #        self.pool_caps = pool_caps
# #        self.caps = [
# #            sum([pool.caps[idx] for pool in pool_caps.values()])
# #            for idx in range(len(self.feerates))]
# #
# #    def calc_stablefeerate(self, ratio_thresh):
# #        stablefeerate = None
# #        for idx in range(len(self.feerates)-1, -1, -1):
# #            try:
# #                rate_ratio = self.tx_byterates[idx] / self.caps[idx]
# #            except ZeroDivisionError:
# #                break
# #            else:
# #                if rate_ratio <= ratio_thresh:
# #                    stablefeerate = self.feerates[idx]
# #
# #        return stablefeerate
# #
# #    def print_caps(self):
# #        table = Table()
# #        table.add_row(("Feerate", "TxRate", "Capacity"))
# #        for idx in range(len(self.feerates)):
# #            table.add_row((
# #                self.feerates[idx],
# #                '%.2f' % self.tx_byterates[idx],
# #                '%.2f' % self.caps[idx]))
# #        table.print_table()
# #
# #
# #class PoolCapacity(object):
# #    def __init__(self, feerates, blockrate, pool):
# #        self.feerates = feerates
# #        self.procrates = [0.]*len(feerates)
# #        self.caps = [0.]*len(feerates)
# #        self.proportion = pool.proportion
# #        self.maxcap = pool.maxblocksize*blockrate*pool.proportion
# #        self.minfeerate = pool.minfeerate
# #        self.update_capacities()
# #
# #    def update_capacities(self):
# #        residualcap = self.maxcap
# #        for idx in range(len(self.feerates)-1, -1, -1):
# #            f = self.feerates[idx]
# #            self.caps[idx] = residualcap if f >= self.minfeerate else 0.
# #            residualcap = max(self.caps[idx]-self.procrates[idx], 0)
# #
# #    def print_caps(self):
# #        table = Table()
# #        table.add_row(("Feerate", "ProcRate", "Capacity"))
# #        for idx in range(len(self.feerates)):
# #            table.add_row((
# #                self.feerates[idx],
# #                '%.2f' % self.procrates[idx],
# #                '%.2f' % self.caps[idx]))
# #        table.print_table()
