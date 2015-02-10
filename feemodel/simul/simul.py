from __future__ import division

from collections import defaultdict
from bisect import insort
from time import time
from copy import copy

from feemodel.simul.stats import Capacity

rate_ratio_thresh = 0.9


class Simul(object):
    def __init__(self, pools, tx_source):
        self.pools = pools
        self.tx_source = tx_source
        feerates, cap_lower, cap_upper = self.pools.get_capacity()
        tx_byterates = tx_source.get_byterates(feerates)
        self.cap = Capacity(feerates, tx_byterates, cap_lower, cap_upper)
        self.stablefeerate = self.cap.calc_stablefeerate(rate_ratio_thresh)
        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")
        self.mempool = None

    def run(self, mempool=None):
        # miniters takes precedence over maxtime.
        if mempool is None:
            mempool = []
        self.mempool = SimMempool(mempool)
        starttime = time()
        for simblock in self.pools.blockgen():
            elapsedrealtime = time() - starttime
            newtxs = self.tx_source.generate_txs(simblock.interval)
            newtxs = filter(lambda tx: tx.feerate >= self.stablefeerate,
                            newtxs)
            self.mempool._add_txs(newtxs)
            self.mempool._process_block(simblock)
            simblock.sfr = max(simblock.sfr, self.stablefeerate)

            yield simblock, elapsedrealtime


class SimMempool(object):
    def __init__(self, mempool):
        self._tx_nodeps = []
        self._tx_havedeps = {}
        self._depmap = defaultdict(list)

        for simtx in mempool:
            if not simtx._depends:
                self._tx_nodeps.append(simtx)
            else:
                for dep in simtx._depends:
                    self._depmap[dep].append(simtx._id)
                self._tx_havedeps[simtx._id] = simtx

        self._tx_nodeps_bak = self._tx_nodeps[:]
        self._tx_havedeps_bak = self._tx_havedeps.values()
        self._deps_bak = [tx._depends[:] for tx in self._tx_havedeps_bak]

    @property
    def txs(self):
        return [copy(tx)
                for tx in self._tx_nodeps + self._tx_havedeps.values()]

    def reset(self):
        self._tx_nodeps = self._tx_nodeps_bak[:]
        for idx, tx in enumerate(self._tx_havedeps_bak):
            tx._depends = self._deps_bak[idx][:]
        self._tx_havedeps = {tx._id: tx for tx in self._tx_havedeps_bak}

    def calc_size(self):
        numbytes = (sum([tx.size for tx in self._tx_nodeps]) +
                    sum([tx.size for tx in self._tx_havedeps.values()]))
        numtxs = len(self._tx_nodeps) + len(self._tx_havedeps)
        return numbytes, numtxs

    def _add_txs(self, newtxs):
        self._tx_nodeps.extend(newtxs)

    def _process_block(self, simblock):
        maxblocksize = simblock.poolinfo[1].maxblocksize
        minfeerate = simblock.poolinfo[1].minfeerate
        blocksize = 0
        sfr = float("inf")
        blocksize_ltd = 0

        self._tx_nodeps.sort(key=lambda x: x.feerate)
        rejected_txs = []
        blocktxs = []
        while self._tx_nodeps:
            newtx = self._tx_nodeps.pop()
            if newtx.feerate >= minfeerate:
                if newtx.size + blocksize <= maxblocksize:
                    if blocksize_ltd > 0:
                        blocksize_ltd -= 1
                    else:
                        sfr = min(newtx.feerate, sfr)

                    blocktxs.append(newtx)
                    blocksize += newtx.size

                    dependants = self._depmap.get(newtx._id)
                    if dependants:
                        for txid in dependants:
                            deptx = self._tx_havedeps[txid]
                            deptx._depends.remove(newtx._id)
                            if not deptx._depends:
                                insort(self._tx_nodeps, deptx)
                                del self._tx_havedeps[txid]
                else:
                    rejected_txs.append(newtx)
                    blocksize_ltd += 1
            else:
                rejected_txs.append(newtx)
                break
        self._tx_nodeps.extend(rejected_txs)

        simblock.sfr = sfr if blocksize_ltd else minfeerate
        simblock.is_sizeltd = bool(blocksize_ltd)
        simblock.size = blocksize
        simblock.txs = blocktxs

    def __repr__(self):
        return ("SimMempool{numbytes: %d, numtxs: %d}" % self.calc_size())
