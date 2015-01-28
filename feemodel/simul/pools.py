from random import random
from math import log, exp
from copy import copy
from bisect import bisect_left


class Pools(object):
    def __init__(self, init_pools=None):
        self._pools = {}
        self._poolsidx = []
        self._pools_sorted = []

        if init_pools:
            self.update(init_pools)

    def next_block(self):
        poolidx = bisect_left(self._poolsidx, random())
        name, pool = self._pools_sorted[poolidx]
        return pool.maxblocksize, pool.minfeerate

    def calc_capacities(self, tx_source, blockrate):
        mfrs = sorted(set([pool.minfeerate for pool in self._pools.values()]))
        mfrs.insert(0, 0)
        tx_byterates = tx_source.get_byterates(mfrs)
        pool_caps = {
            name: PoolCapacity(mfrs, blockrate, pool)
            for name, pool in self._pools.items()}
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

    def update(self, pools_dict):
        self._pools.update(pools_dict)
        self._calc_idx()

    def remove(self, poolname):
        try:
            del self._pools[poolname]
        except KeyError:
            raise KeyError("No such pool.")
        self._calc_idx()

    def getpools(self):
        return {name: copy(pool) for name, pool in self}

    def print_pools(self):
        poolitems = sorted(self._pools.items(),
                           key=lambda p: p[1], reverse=True)
        names = [name for name, pool in poolitems]
        maxblocksizes = [str(pool.maxblocksize) for name, pool in poolitems]
        minfeerates = [str(pool.minfeerate) for name, pool in poolitems]
        proportions = [pool.proportion for name, pool in poolitems]

        namefmt = '%' + str(max([len(name) for name in names])) + 's\t'
        mbsfmt = '%' + str(max([len(mbs) for mbs in maxblocksizes])) + 's\t'
        mfrfmt = '%' + str(max([len(mfr) for mfr in minfeerates])) + 's\t'

        print((namefmt + '%5s\t' + mbsfmt + mfrfmt) %
              ('Name', 'Prop', 'MBS', 'MFR'))
        for idx in range(len(poolitems)):
            print((namefmt + '%5.2f\t' + mbsfmt + mfrfmt) %
                  (names[idx], proportions[idx],
                  maxblocksizes[idx], minfeerates[idx]))

    def __iter__(self):
        return self._pools.iteritems()

    def __repr__(self):
        elogp = sum([p.proportion*log(p.proportion)
                     for p in self._pools.values()])
        numeffpools = exp(elogp)
        return "Pools{Num: %d, NumEffective: %.2f}" % (
            len(self._pools), numeffpools)

    def _calc_idx(self):
        self._calc_proportions()
        self._poolsidx = []
        self._pools_sorted = []
        poolitems = sorted(self._pools.items(),
                           key=lambda p: p[1], reverse=True)
        p = 0.
        for name, pool in poolitems:
            p += pool.proportion
            self._poolsidx.append(p)
            self._pools_sorted.append((name, pool))

        self._poolsidx[-1] = 1.

    def _calc_proportions(self):
        totalhashrate = float(sum(
            [pool.hashrate for pool in self._pools.values()]))
        if not totalhashrate:
            raise ValueError("No pools.")
        for pool in self._pools.values():
            pool.proportion = pool.hashrate / totalhashrate


class Pool(object):
    def __init__(self, hashrate, maxblocksize, minfeerate):
        assert hashrate > 0
        assert maxblocksize > 0
        assert minfeerate > 0
        self.hashrate = hashrate
        self.maxblocksize = maxblocksize
        self.minfeerate = minfeerate
        self.proportion = None

    def __cmp__(self, other):
        return cmp(self.hashrate, other.hashrate)

    def __repr__(self):
        return ("Pool{hashrate: %.2f, maxblocksize: %d, minfeerate: %d}" %
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
        print("Feerate\tTxRate\tCapacity")
        for idx in range(len(self.feerates)):
            print("%d\t%.1f\t%.1f" %
                  (self.feerates[idx], self.tx_byterates[idx], self.caps[idx]))


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
