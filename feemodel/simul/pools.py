from __future__ import division

from random import random, expovariate, choice
from bisect import bisect_left
from itertools import groupby
from operator import attrgetter
from collections import Counter

from tabulate import tabulate

from feemodel.util import cumsum_gen, StepFunction
from feemodel.simul.simul import SimBlock

DEFAULT_BLOCKRATE = 1./600


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


class SimPoolsNP(object):

    def __init__(self, maxblocksizes, minfeerates,
                 blockrate=DEFAULT_BLOCKRATE):
        self.maxblocksizes = maxblocksizes
        self.minfeerates = minfeerates
        self.blockrate = blockrate

    def check(self):
        for attr in ['maxblocksizes', 'minfeerates', 'blockrate']:
            if not getattr(self, attr):
                raise ValueError("{} must be nonzero.".format(attr))

    def blockgen(self):
        self.check()
        while True:
            blockinterval = expovariate(self.blockrate)
            maxblocksize = choice(self.maxblocksizes)
            minfeerate = choice(self.minfeerates)
            pool = SimPool(1, maxblocksize, minfeerate)
            simblock = SimBlock('', pool)
            yield simblock, blockinterval

    def get_capacityfn(self):
        def cap_mapfn(y):
            return (y*expected_maxblocksize*self.blockrate /
                    len(self.minfeerates))

        expected_maxblocksize = (
            sum(self.maxblocksizes) / len(self.maxblocksizes))
        cap_fn = self.get_hashratefn()
        cap_fn._y = map(cap_mapfn, cap_fn._y)

        return cap_fn

    def get_hashratefn(self):
        self.check()
        feerates_all = filter(lambda f: f < float("inf"), self.minfeerates)
        feerates_count = Counter(feerates_all)
        feerates, counts = map(list, zip(*sorted(feerates_count.items())))

        caps = list(cumsum_gen(counts))
        feerates.insert(0, feerates[0]-1)
        caps.insert(0, 0)

        return StepFunction(feerates, caps)

    def calc_totalhashrate(self):
        return len(self.minfeerates)

    def __nonzero__(self):
        try:
            self.check()
        except ValueError:
            return False
        return True

    def __eq__(self, other):
        return all([
            getattr(self, attr) == getattr(other, attr)
            for attr in ['maxblocksizes', 'minfeerates', 'blockrate']])


class SimPools(object):

    def __init__(self, pools, blockrate=DEFAULT_BLOCKRATE):
        self.pools = pools
        self.blockrate = blockrate

    def check(self):
        if not self.pools:
            raise ValueError("No pools.")
        if any([pool.hashrate <= 0 or
                pool.maxblocksize <= 0 or
                pool.minfeerate < 0
                for pool in self.pools.values()]):
            raise ValueError("Bad pool stats.")
        if not any([pool.minfeerate < float("inf")
                    for pool in self.pools.values()]):
            raise ValueError("Zero pools capacity.")

    def blockgen(self):
        self.check()
        poolitems = sorted(self.pools.items(),
                           key=lambda item: item[1].hashrate)
        cumhashrates = list(
            cumsum_gen([pool.hashrate for name, pool in poolitems]))
        totalhashrate = cumhashrates[-1]
        prop_table = map(lambda hashrate: hashrate/totalhashrate,
                         cumhashrates)
        while True:
            poolidx = bisect_left(prop_table, random())
            simblock = SimBlock(*poolitems[poolidx])
            blockinterval = expovariate(self.blockrate)
            yield simblock, blockinterval

    def get_capacityfn(self):
        """Get cumulative capacity as function of minfeerate."""
        self.check()
        totalhashrate = self.calc_totalhashrate()

        def byterate_groupsum(grouptuple):
            return sum(
                [pool.hashrate*pool.maxblocksize
                 for pool in grouptuple[1]])*self.blockrate/totalhashrate

        pools = filter(
            lambda pool: pool.minfeerate < float("inf"),
            sorted(self.pools.values(), key=attrgetter("minfeerate")))
        feerates = sorted(set(map(attrgetter("minfeerate"), pools)))
        caps = list(cumsum_gen(
            groupby(pools, attrgetter("minfeerate")), mapfn=byterate_groupsum))

        feerates.insert(0, feerates[0]-1)
        caps.insert(0, 0)

        return StepFunction(feerates, caps)

    def get_hashratefn(self):
        """Get cumulative hashrate as function of minfeerate."""
        self.check()

        def hashrate_groupsum(grouptuple):
            return sum(map(attrgetter("hashrate"), grouptuple[1]))

        pools = filter(
            lambda pool: pool.minfeerate < float("inf"),
            sorted(self.pools.values(), key=attrgetter("minfeerate")))
        feerates = sorted(set(map(attrgetter("minfeerate"), pools)))
        hashrates = list(cumsum_gen(
            groupby(pools, attrgetter("minfeerate")), mapfn=hashrate_groupsum))

        feerates.insert(0, feerates[0]-1)
        hashrates.insert(0, 0)

        return StepFunction(feerates, hashrates)

    def calc_totalhashrate(self):
        return sum([pool.hashrate for pool in self.pools.values()])

    def __nonzero__(self):
        try:
            self.check()
        except ValueError:
            return False
        return True

    def __repr__(self):
        try:
            capfn = self.get_capacityfn()
        except ValueError:
            maxcap = 0
        else:
            maxcap = capfn[-1][1]
        return ("SimPools(Num: {}, MaxCap: {})".
                format(len(self.pools), maxcap))

    def __str__(self):
        try:
            self.check()
        except ValueError as e:
            return e.message
        poolitems = sorted(self.pools.items(),
                           key=lambda pitem: pitem[1].hashrate, reverse=True)
        totalhashrate = self.calc_totalhashrate()
        headers = ["Name", "HR", "Prop", "MBS", "MFR"]
        table = [(name,
                  pool.hashrate,
                  pool.hashrate / totalhashrate,
                  pool.maxblocksize,
                  pool.minfeerate)
                 for name, pool in poolitems]
        poolstats = tabulate(table, headers=headers)
        meanblocksize = sum([prop*mbs for _0, _1, prop, mbs, _2 in table])
        maxcap = meanblocksize*self.blockrate

        table = [
            ("Block interval (s)", 1 / self.blockrate),
            ("Total hashrate", totalhashrate),
            ("Max capacity (bytes/s)", maxcap)
        ]
        miscstats = tabulate(table)
        return poolstats + '\n' + miscstats

    def __eq__(self, other):
        return self.pools == other.pools

    def __ne__(self, other):
        return self.pools != other.pools
