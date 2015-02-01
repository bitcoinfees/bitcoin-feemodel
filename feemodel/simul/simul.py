from collections import defaultdict
from bisect import insort
from time import time

from feemodel.simul.txsources import SimTx

rate_ratio_thresh = 0.9


class Simul(object):
    def __init__(self, pools, tx_source):
        self.pools = pools
        self.tx_source = tx_source
        self.cap = self.pools.calc_capacities(self.tx_source)
        self.stablefeerate = self.cap.calc_stablefeerate(rate_ratio_thresh)
        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")
        self.mempool = None

    def run(self, mempool=None, maxiters=10000, maxtime=60):
        if mempool is None:
            mempool = {}
        self.mempool = SimMempool(mempool)
        starttime = time()
        for simblock in self.pools.blockgen():
            elapsedrealtime = time() - starttime
            if elapsedrealtime > maxtime:
                break
            if simblock.height >= maxiters:
                break
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

        for txid, entry in mempool.items():
            simtx = SimTx(entry.size, entry.feerate, txid, entry.depends)
            if not simtx._depends:
                self._tx_nodeps.append(simtx)
            else:
                for dep in simtx._depends:
                    self._depmap[dep].append(txid)
                self._tx_havedeps[txid] = simtx

        self._tx_nodeps_bak = self._tx_nodeps[:]
        self._tx_havedeps_bak = self._tx_havedeps.values()
        self._deps_bak = [tx._depends[:] for tx in self._tx_havedeps_bak]

    # Use a property
    def get_txs(self):
        mempool_txs = self._tx_nodeps[:] + self._tx_havedeps.values()
        return mempool_txs

    def reset(self):
        self._tx_nodeps = self._tx_nodeps_bak[:]
        for idx, tx in enumerate(self._tx_havedeps_bak):
            tx._depends = self._deps_bak[idx][:]
        self._tx_havedeps = {tx._id: tx for tx in self._tx_havedeps_bak}

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

    def _calc_size(self):
        numbytes = (sum([tx.size for tx in self._tx_nodeps]) +
                    sum([tx.size for tx in self._tx_havedeps.values()]))
        numtxs = len(self._tx_nodeps) + len(self._tx_havedeps)
        return numbytes, numtxs

    def __repr__(self):
        return ("SimMempool{numbytes: %d, numtxs: %d}" % self._calc_size())
