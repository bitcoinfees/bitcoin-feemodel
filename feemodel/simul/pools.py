from random import random, expovariate
from math import log, exp
from copy import deepcopy, copy
from bisect import bisect_left
from feemodel.util import Table

default_blockrate = 1./600


class SimBlock(object):
    def __init__(self, blockheight, blocktime, blockinterval, poolinfo):
        self.height = blockheight
        self.size = 0
        self.time = blocktime
        self.interval = blockinterval
        self.poolinfo = poolinfo
        self.sfr = float("inf")
        self.is_sizeltd = None

        self._txs = []
        self._txs_copied = False

    @property
    def txs(self):
        # Only make a copy of the txs if it is accessed, for efficiency.
        # If the attribute was not accessed in the current sim iteration,
        # then the tx object might change (specifically the _depends attr).
        if not self._txs_copied:
            self._txs_copied = True
            self._txs = [copy(tx) for tx in self._txs]
        return self._txs

    @txs.setter
    def txs(self, val):
        # The block txs are set in SimMempool._process_blocks.
        # We defer making copies of the SimTx objects until they are
        # accessed.
        self._txs_copied = False
        self._txs = val

    def __repr__(self):
        return "SimBlock{height: %d, numtxs: %d, size: %s, sfr: %.0f" % (
            self.height, len(self.txs), self.size, self.sfr)


class SimPools(object):
    def __init__(self, pools=None, blockrate=default_blockrate):
        self.blockrate = blockrate
        self.__pools = []
        self.__poolsidx = []

        if pools:
            self.update(pools)

    def blockgen(self):
        def blockgenfn():
            simtime = 0.
            blockheight = 0
            while True:
                poolidx = bisect_left(self.__poolsidx, random())
                poolinfo = self.__pools[poolidx]
                blockinterval = expovariate(self.blockrate)
                simtime += blockinterval
                simblock = SimBlock(blockheight, simtime,
                                    blockinterval, poolinfo)
                blockheight += 1
                yield simblock

        return blockgenfn()

    def get(self):
        return {name: deepcopy(pool) for name, pool in self.__pools}

    def update(self, pools):
        poolitems = sorted(
            [(name, deepcopy(pool)) for name, pool in pools.items()],
            key=lambda p: p[1], reverse=True)
        totalhashrate = float(sum(
            [pool.hashrate for name, pool in poolitems]))
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

    def calc_capacities(self, tx_source):
        poolfeerates = [pool.minfeerate for name, pool in self.__pools]
        poolfeerates = sorted(set(poolfeerates + [0]))
        poolfeerates = filter(lambda fee: fee < float("inf"), poolfeerates)
        tx_byterates = tx_source.get_byterates(poolfeerates)
        pool_caps = {
            name: PoolCapacity(poolfeerates, self.blockrate, pool)
            for name, pool in self.__pools}
        for idx in range(len(poolfeerates)-1, -1, -1):
            excessrate = tx_byterates[idx]
            while excessrate > 0:
                nonmaxedpools = filter(
                    lambda pool: pool.procrates[idx] < pool.caps[idx],
                    pool_caps.values())
                if not nonmaxedpools:
                    break
                totalprop = sum([
                    pool.proportion for pool in nonmaxedpools])
                for pool in nonmaxedpools:
                    ratealloc = pool.proportion * excessrate / totalprop
                    pool.procrates[idx] += ratealloc
                    pool.procrates[idx] = min(pool.procrates[idx],
                                              pool.caps[idx])
                excessrate = tx_byterates[idx] - sum([
                    pool.procrates[idx] for pool in pool_caps.values()])
            for pool in pool_caps.values():
                pool.update_capacities()

        return Capacity(poolfeerates, tx_byterates, pool_caps)

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
        return "SimPools{Num: %d, NumEffective: %.2f}" % (
            len(self.__pools), numeffpools)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


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
            sum([pool.caps[idx] for pool in pool_caps.values()])
            for idx in range(len(self.feerates))]

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
        table = Table()
        table.add_row(("Feerate", "TxRate", "Capacity"))
        for idx in range(len(self.feerates)):
            table.add_row((
                self.feerates[idx],
                '%.2f' % self.tx_byterates[idx],
                '%.2f' % self.caps[idx]))
        table.print_table()


class PoolCapacity(object):
    def __init__(self, feerates, blockrate, pool):
        self.feerates = feerates
        self.procrates = [0.]*len(feerates)
        self.caps = [0.]*len(feerates)
        self.proportion = pool.proportion
        self.maxcap = pool.maxblocksize*blockrate*pool.proportion
        self.minfeerate = pool.minfeerate
        self.update_capacities()

    def update_capacities(self):
        residualcap = self.maxcap
        for idx in range(len(self.feerates)-1, -1, -1):
            f = self.feerates[idx]
            self.caps[idx] = residualcap if f >= self.minfeerate else 0.
            residualcap = max(self.caps[idx]-self.procrates[idx], 0)

    def print_caps(self):
        table = Table()
        table.add_row(("Feerate", "ProcRate", "Capacity"))
        for idx in range(len(self.feerates)):
            table.add_row((
                self.feerates[idx],
                '%.2f' % self.procrates[idx],
                '%.2f' % self.caps[idx]))
        table.print_table()
