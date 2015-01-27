from collections import defaultdict
from bisect import insort
from time import time
from random import expovariate

from bitcoin.core import COIN
from feemodel.queuestats import QueueStats
from feemodel.simul.txsources import SimTx
from feemodel.util import DataSample

rate_ratio_thresh = 0.9
default_blockrate = 1./600


class Simul(object):
    def __init__(self, pools, tx_source, blockrate=default_blockrate):
        self.pools = pools
        self.tx_source = tx_source
        self.blockrate = blockrate

    def steady_state(self, maxiters=100000, maxtime=60,
                     feeclasses=None, mempool_entries=None, stopflag=None):
        if not mempool_entries:
            mempool_entries = {}
        self.mempool = SimMempool(mempool_entries, self.pools,
                                  self.tx_source, self.blockrate)

        if not feeclasses:
            self._get_feeclasses()
        else:
            self.feeclasses = feeclasses
        qstats = QueueStats(self.feeclasses)

        starttime = time()
        for i in xrange(maxiters):
            if stopflag and stopflag.is_set():
                raise ValueError("Simulation terminated.")
            elapsedtime = time() - starttime
            if elapsedtime > maxtime:
                break
            blockinterval, stranding_feerate = self.mempool.next_block()
            qstats.next_block(i, blockinterval, stranding_feerate)

        return qstats.stats, elapsedtime, i+1

    def _get_feeclasses(self):
        if not self.mempool:
            raise ValueError("Mempool not yet initialized.")
        feerates = self.mempool.cap_stats.feerates[1:]
        caps = self.mempool.cap_stats.capacities
        capsdiff = [caps[idx] - caps[idx-1]
                    for idx in range(1, len(feerates)+1)]
        feeDS = DataSample(feerates)
        self.feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
                           for p in range(5, 100, 5)]


class SimMempool(object):
    def __init__(self, entries, pools, tx_source, blockrate):
        self.pools = pools
        self.tx_source = tx_source
        self.blockrate = blockrate
        self.tx_nodeps = []
        self.tx_havedeps = {}
        self.depmap = defaultdict(list)

        for txid, entry in entries.items():
            if 'feerate' not in entry:
                entry['feerate'] = int(entry['fee']*COIN)*1000 // entry['size']
            if not entry['depends']:
                self.tx_nodeps.append(
                    SimTx(txid, entry['size'], entry['feerate']))
            else:
                for dep in entry['depends']:
                    self.depmap[dep].append(txid)
                self.tx_havedeps[txid] = (
                    SimTx(txid, entry['size'], entry['feerate']),
                    entry['depends'])

        self.tx_nodeps_bak = self.tx_nodeps[:]
        self.tx_havedeps_bak = {txid: (tx[0], tx[1][:])
                                for txid, tx in self.tx_havedeps.items()}
        self._calc_stablefeerate()

    def next_block(self):
        blockinterval = expovariate(self.blockrate)
        newtxs = self.tx_source.generate_txs(blockinterval)
        newtxs = filter(lambda tx: tx.feerate >= self.stablefeerate)
        self.tx_nodeps.extend(newtxs)
        maxblocksize, minfeerate = self.pools.next_block()
        stranding_feerate = self._process_block(maxblocksize, minfeerate)
        return blockinterval, stranding_feerate

    def reset(self):
        self.tx_nodeps = self.tx_nodeps_bak[:]
        self.tx_havedeps = {txid: (tx[0], tx[1][:])
                            for txid, tx in self.tx_havedeps_bak}

    def _process_block(self, maxblocksize, minfeerate):
        blocksize = 0
        sfr = float("inf")
        blocksize_ltd = 0

        self.tx_nodeps.sort(key=lambda x: x.feerate)
        rejected_txs = []
        while self.tx_nodeps:
            newtx = self.tx_nodeps.pop()
            if newtx.feerate >= minfeerate:
                if newtx.size + blocksize <= maxblocksize:
                    if blocksize_ltd > 0:
                        blocksize_ltd -= 1
                    else:
                        sfr = min(newtx.feerate, sfr)

                    blocksize += newtx.size

                    dependants = self.depmap.get(newtx.txid)
                    if dependants:
                        for txid in dependants:
                            deptx = self.tx_havedeps[txid]
                            deptx[1].remove(newtx.txid)
                            if not deptx[1]:
                                insort(self.tx_nodeps, deptx[0])
                                del self.tx_havedeps[txid]
                else:
                    rejected_txs.append(newtx)
                    blocksize_ltd += 1
            else:
                rejected_txs.append(newtx)
                break

        self.tx_nodeps.extend(rejected_txs)

        return sfr if blocksize_ltd else minfeerate

    def _calc_stablefeerate(self):
        self.cap_stats = self.pools.calc_capacities(self.tx_source)
        self.stablefeerate = None
        for feerate, tx_byterate, cap in sorted(zip(*self.cap_stats),
                                                reverse=True):
            rate_ratio = tx_byterate / cap if cap else float("inf")
            if rate_ratio <= rate_ratio_thresh:
                self.stablefeerate = feerate

        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")

    def _calc_size(self):
        numbytes = (sum([tx.size for tx in self.tx_nodeps]) +
                    sum([tx.size for tx in self.tx_havedeps]))
        numtxs = len(self.tx_nodeps) + len(self.tx_havedeps)
        return numbytes, numtxs

    def __repr__(self):
        return ("SimMempool{numbytes: %d, numtxs: %d}" % self._calc_size())


class SteadyStateStats(object):
    pass
# #def _spacefees(feepoints, stablefeerate, minspacing=1000):
# #    feepoints.sort(reverse=True)
# #    prevfeerate = feepoints[0]
# #    feepoints_spaced = [prevfeerate]
# #    for feerate in feepoints[1:]:
# #        if feerate < stablefeerate:
# #            break
# #        if prevfeerate - feerate >= minspacing:
# #            feepoints_spaced.append(feerate)
# #            prevfeerate = feerate
# #
# #    feepoints_spaced.sort()
# #    return feepoints_spaced
