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
