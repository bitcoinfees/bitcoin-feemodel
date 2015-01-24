from collections import defaultdict
from bisect import insort
from time import time

from bitcoin.core import COIN
from feemodel.queuestats import QueueStats
from feemodel.simul.txsources import SimTx

rate_ratio_thresh = 0.9


class SimMempool(object):
    def __init__(self, entries):
        self.tx_nodeps = []
        self.tx_havedeps = {}
        self.depmap = defaultdict(list)
        if not entries:
            return

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

        self.tx_nodeps.sort(key=lambda x: x.feerate)

    def add_txs(self, simtxs):
        self.tx_nodeps.extend(simtxs)

    def process_block(self, maxblocksize, minfeerate):
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

    def _calc_size(self):
        numbytes = (sum([tx.size for tx in self.tx_nodeps]) +
                    sum([tx.size for tx in self.tx_havedeps]))
        numtxs = len(self.tx_nodeps) + len(self.tx_havedeps)
        return numbytes, numtxs

    def __repr__(self):
        return ("SimMempool{numbytes: %d, numtxs: %d}" % self._calc_size())


class Simul(object):
    def __init__(self, miner, tx_source):
        self.miner = miner
        self.tx_source = tx_source

    def init_calcs(self):
        self.cap_stats = self.miner.calc_capacities(self.tx_source)
        self.stablefeerate = None
        for feerate, tx_byterate, cap in sorted(zip(*self.cap_stats),
                                                reverse=True):
            rate_ratio = tx_byterate / cap if cap else float("inf")
            if rate_ratio <= rate_ratio_thresh:
                self.stablefeerate = feerate

        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")

        miner_feepoints = self.miner.get_feepoints()
        tx_feepoints = self.tx_source.get_feepoints()
        feeclasses = miner_feepoints + tx_feepoints
        self.feeclasses = _spacefees(feeclasses, self.stablefeerate)

    def steady_state(self, miniters=10000, maxiters=100000, maxtime=60,
                     mempool=None, stopflag=None):
        self.init_calcs()
        mempool = SimMempool(mempool)
        qstats = QueueStats(self.feeclasses)
        starttime = time()
        for i in xrange(maxiters):
            if stopflag and stopflag.is_set():
                raise ValueError("Simulation terminated.")
            elapsedtime = time() - starttime
            if elapsedtime > maxtime:
                break
            bi, mbs, mfr = self.miner.next_block_policy()
            newtxs = self.tx_source.generate_txs(bi, self.stablefeerate)
            mempool.add_txs(newtxs)
            sfr = mempool.process_block(mbs, mfr)
            qstats.next_block(i, bi, sfr)

        i += 1
        if i < miniters:
            raise ValueError("Too few iterations in the allotted time.")

        return qstats.stats, elapsedtime, i


def _spacefees(feepoints, stablefeerate, minspacing=1000):
    feepoints.sort(reverse=True)
    prevfeerate = feepoints[0]
    feepoints_spaced = [prevfeerate]
    for feerate in feepoints[1:]:
        if feerate < stablefeerate:
            break
        if prevfeerate - feerate >= minspacing:
            feepoints_spaced.append(feerate)
            prevfeerate = feerate

    feepoints_spaced.sort()
    return feepoints_spaced
