from random import random
from math import log, exp
from copy import copy
from bisect import bisect_left
from feemodel.util import Table


# Change the structure to match PoolsEstimator: dump all the 'get' stuff
class SimPools(object):
    def __init__(self, pools=None):
        self.__pools = []
        self.__poolsidx = []

        if pools:
            self.update(pools)

    def next_block(self):
        poolidx = bisect_left(self.__poolsidx, random())
        name, pool = self.__pools[poolidx]
        return name, pool.maxblocksize, pool.minfeerate

    def update(self, pools):
        poolitems = sorted(
            [(name, copy(pool)) for name, pool in pools.items()],
            key=lambda p: p[1], reverse=True)
        totalhashrate = float(sum(
            [pool.hashrate for name, pool in poolitems]))
        if not totalhashrate:
            raise ValueError("No pools.")

        self.__poolsidx = []
        self.__pools = []
        cumprop = 0.
        for name, pool in poolitems:
            for attr in ['hashrate', 'maxblocksize', 'minfeerate']:
                assert getattr(pool, attr) > 0
            pool.proportion = pool.hashrate / totalhashrate
            cumprop += pool.proportion
            self.__poolsidx.append(cumprop)
            self.__pools.append((name, pool))

        self.__poolsidx[-1] = 1.

    def calc_capacities(self, tx_source, blockrate):
        mfrs = sorted(set([pool.minfeerate for name, pool in self.__pools]))
        mfrs = filter(lambda fee: fee < float("inf"), mfrs)
        mfrs.insert(0, 0)
        tx_byterates = tx_source.get_byterates(mfrs)
        pool_caps = {
            name: PoolCapacity(mfrs, blockrate, pool)
            for name, pool in self.__pools}
        for feerate, byterate in reversed(zip(mfrs, tx_byterates)):
            excessrate = byterate
            while excessrate > 0:
                nonmaxedpools = filter(
                    lambda pool: (pool.caps[feerate][0] <
                                  pool.caps[feerate][1]),
                    pool_caps.values())
                if not nonmaxedpools:
                    break
                totalproportion = sum([
                    pool.proportion for pool in nonmaxedpools])
                for pool in nonmaxedpools:
                    ratealloc = pool.proportion * excessrate / totalproportion
                    pool.caps[feerate][0] += ratealloc
                    pool.caps[feerate][0] = min(pool.caps[feerate][0],
                                                pool.caps[feerate][1])
                excessrate = byterate - sum([
                    pool.caps[feerate][0] for pool in pool_caps.values()])
            for pool in pool_caps.values():
                pool.update_capacities()

        return Capacity(mfrs, tx_byterates, pool_caps)

    def print_pools(self):
        maxnamelen = max([len(name) for name, pool in self.__pools])
        colwidths = (maxnamelen, 10.2, 10, 10.0)
        coltypes = 'sfdf'
        table = Table(colwidths, coltypes)
        table.print_header("Name", "Prop", "MBS", "MFR")
        for name, pool in self.__pools:
            table.print_row(name, pool.proportion,
                            pool.maxblocksize, pool.minfeerate)

    def __repr__(self):
        elogp = sum([p.proportion*log(p.proportion)
                     for n, p in self.__pools])
        numeffpools = exp(elogp)
        return "SimPools{Num: %d, NumEffective: %.2f}" % (
            len(self.__pools), numeffpools)


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


class Capacity(object):
    def __init__(self, feerates, tx_byterates, pool_caps):
        self.feerates = feerates
        self.tx_byterates = tx_byterates
        self.pool_caps = pool_caps
        self.caps = [
            sum([pool.caps[f][1] for pool in pool_caps.values()])
            for f in feerates]

    def calc_stablefeerate(self, ratio_thresh):
        stablefeerate = None
        for idx in range(len(self.feerates)-1, -1, -1):
            try:
                rate_ratio = self.tx_byterates[idx] / self.caps[idx]
            except ZeroDivisionError:
                break
            else:
                if rate_ratio <= ratio_thresh:
                    stablefeerate = self.feerates[idx]

        return stablefeerate

    def print_caps(self):
        colwidths = (10, 10.2, 10.2)
        coltypes = 'dff'
        table = Table(colwidths, coltypes)
        table.print_header("Feerate", "TxRate", "Capacity")
        for idx in range(len(self.feerates)):
            table.print_row(
                self.feerates[idx], self.tx_byterates[idx], self.caps[idx])


class PoolCapacity(object):
    def __init__(self, feerates, blockrate, pool):
        self.caps = {feerate: [0., 0.] for feerate in feerates}
        self.proportion = pool.proportion
        self.maxcap = pool.maxblocksize*blockrate*pool.proportion
        self.minfeerate = pool.minfeerate
        self.update_capacities()

    def update_capacities(self):
        feerates = sorted(self.caps.keys(), reverse=True)
        residualcap = self.maxcap
        for f in feerates:
            self.caps[f][1] = residualcap if f >= self.minfeerate else 0.
            residualcap = max(self.caps[f][1] - self.caps[f][0], 0)
