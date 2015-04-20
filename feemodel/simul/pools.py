from __future__ import division

from random import random, expovariate
from bisect import bisect_left
from itertools import groupby

from feemodel.util import cumsum_gen
from feemodel.simul.simul import SimBlock

default_blockrate = 1./600


class SimPool(object):
    def __init__(self, hashrate, maxblocksize, minfeerate):
        self.hashrate = hashrate
        self.maxblocksize = maxblocksize
        self.minfeerate = minfeerate

    def __cmp__(self, other):
        # TODO: deprecate this.
        raise NotImplementedError

    def __repr__(self):
        return ("SimPool{hashrate: %.2f, maxblocksize: %d, minfeerate: %.0f}" %
                (self.hashrate, self.maxblocksize, self.minfeerate))

    def __eq__(self, other):
        relevant_attrs = ['hashrate', 'maxblocksize', 'minfeerate']
        return all([
            getattr(self, attr) == getattr(other, attr)
            for attr in relevant_attrs])

    def __ne__(self, other):
        relevant_attrs = ['hashrate', 'maxblocksize', 'minfeerate']
        return any([
            getattr(self, attr) != getattr(other, attr)
            for attr in relevant_attrs])


class SimPools(object):

    def __init__(self, pools, blockrate=default_blockrate):
        self.pools = pools
        self.blockrate = blockrate

    def check_pools(self):
        if not self.pools:
            raise ValueError("No pools.")
        if any([pool.hashrate <= 0 or
                pool.maxblocksize < 0 or
                pool.minfeerate < 0
                for pool in self.pools.values()]):
            raise ValueError("Bad pool stats.")

    def get_blockgen(self):
        self.check_pools()
        poolitems = sorted(self.pools.items(),
                           key=lambda item: item[1].hashrate)
        cumhashrates = list(
            cumsum_gen([pool.hashrate for name, pool in poolitems]))
        totalhashrate = cumhashrates[-1]
        prop_table = map(lambda hashrate: hashrate/totalhashrate,
                         cumhashrates)

        def blockgenfn():
            while True:
                poolidx = bisect_left(prop_table, random())
                simblock = SimBlock(*poolitems[poolidx])
                blockinterval = expovariate(self.blockrate)
                yield simblock, blockinterval

        return blockgenfn()

    def get_capacity(self):
        self.check_pools()
        totalhashrate = self.calc_totalhashrate()

        def minfeerate_keyfn(pool):
            return pool.minfeerate

        def byterate_groupsum(grouptuple):
            return sum([pool.hashrate*pool.maxblocksize/totalhashrate
                        for pool in grouptuple[1]])*self.blockrate

        pools = filter(lambda pool: pool.minfeerate < float("inf"),
                       sorted(self.pools.values(), key=minfeerate_keyfn))
        feerates = sorted(set(map(minfeerate_keyfn, pools)))
        caps = list(cumsum_gen(
            groupby(pools, minfeerate_keyfn), mapfn=byterate_groupsum))

        if not feerates or feerates[0] != 0:
            feerates.insert(0, 0)
            caps.insert(0, 0)

        return feerates, caps

    def calc_totalhashrate(self):
        return sum([pool.hashrate for pool in self.pools.values()])

    def __nonzero__(self):
        try:
            self.check_pools()
        except ValueError:
            return False
        return True

    def __repr__(self):
        try:
            feerates, caps = self.get_capacity()
        except ValueError:
            totalcap = 0
        else:
            totalcap = caps[-1]
        return "SimPools(Num: {}, TotalCap: {})".format(len(self.pools),
                                                        totalcap)

    def __eq__(self, other):
        return self.pools == other.pools

    def __ne__(self, other):
        return self.pools != other.pools

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
