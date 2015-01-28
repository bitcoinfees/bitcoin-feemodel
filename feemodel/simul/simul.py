from collections import defaultdict
from bisect import insort
from random import expovariate

from feemodel.queuestats import QueueStats
from feemodel.simul.txsources import SimTx
from feemodel.util import DataSample, itertimer

rate_ratio_thresh = 0.9
default_blockrate = 1./600


class Simul(object):
    def __init__(self, pools, tx_source, blockrate=default_blockrate):
        self.pools = pools
        self.tx_source = tx_source
        self.blockrate = blockrate
        self.mempool = None

    def steady_state(self, maxiters=100000, maxtime=600,
                     feeclasses=None, stopflag=None, mempool=None):
        if not mempool:
            mempool = {}
        self.mempool = SimMempool(mempool, self.pools,
                                  self.tx_source, self.blockrate)

        if not feeclasses:
            self._get_feeclasses()
        else:
            self.feeclasses = feeclasses

        qstats = QueueStats(self.feeclasses)
        for i, elapsedtime in itertimer(maxiters, maxtime, stopflag):
            blockinterval, stranding_feerate = self.mempool.next_block()
            qstats.next_block(i, blockinterval, stranding_feerate)

        return SteadyStateStats(qstats, self.mempool.cap, elapsedtime,
                                i+1, self.mempool.stablefeerate)

    def transient(self, mempool, maxiters=10000, maxtime=60,
                  feeclasses=None, stopflag=None):
        self.mempool = SimMempool(mempool, self.pools,
                                  self.tx_source, self.blockrate)

        if not feeclasses:
            self._get_feeclasses()
        else:
            self.feeclasses = feeclasses

        tstats = {feerate: DataSample() for feerate in self.feeclasses}
        for i, elapsedtime in itertimer(maxiters, maxtime, stopflag):
            self.mempool.reset()
            stranded = set(self.feeclasses)
            totaltime = 0.
            while stranded:
                blockinterval, stranding_feerate = self.mempool.next_block()
                totaltime += blockinterval
                for feerate in list(stranded):
                    if feerate >= stranding_feerate:
                        tstats[feerate].add_datapoints([totaltime])
                        stranded.remove(feerate)

        return TransientStats(tstats, self.mempool.cap, elapsedtime,
                              i+1, self.mempool.stablefeerate)

    def _get_feeclasses(self):
        if not self.mempool:
            raise ValueError("Mempool not yet initialized.")
        feerates = self.mempool.cap.feerates[1:]
        caps = self.mempool.cap.caps
        capsdiff = [caps[idx] - caps[idx-1]
                    for idx in range(1, len(feerates)+1)]
        feeDS = DataSample(feerates)
        self.feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
                           for p in range(5, 100, 5)]
        self.feeclasses = sorted(set(self.feeclasses))


class SimMempool(object):
    def __init__(self, entries, pools, tx_source, blockrate):
        self.pools = pools
        self.tx_source = tx_source
        self.blockrate = blockrate
        self.tx_nodeps = []
        self.tx_havedeps = {}
        self.depmap = defaultdict(list)
        self.cap = None

        for txid, entry in entries.items():
            if not entry.depends:
                self.tx_nodeps.append(
                    SimTx(txid, entry.size, entry.feerate))
            else:
                for dep in entry.depends:
                    self.depmap[dep].append(txid)
                self.tx_havedeps[txid] = (
                    SimTx(txid, entry.size, entry.feerate),
                    entry.depends[:])

        self.tx_nodeps_bak = self.tx_nodeps[:]
        self.tx_havedeps_bak = {txid: (tx[0], tx[1][:])
                                for txid, tx in self.tx_havedeps.items()}
        self._calc_stablefeerate()

    def next_block(self):
        blockinterval = expovariate(self.blockrate)
        newtxs = self.tx_source.generate_txs(blockinterval)
        newtxs = filter(lambda tx: tx.feerate >= self.stablefeerate, newtxs)
        self.tx_nodeps.extend(newtxs)
        maxblocksize, minfeerate = self.pools.next_block()
        stranding_feerate = self._process_block(maxblocksize, minfeerate)
        return blockinterval, stranding_feerate

    def reset(self):
        self.tx_nodeps = self.tx_nodeps_bak[:]
        self.tx_havedeps = {txid: (tx[0], tx[1][:])
                            for txid, tx in self.tx_havedeps_bak.items()}

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
        self.cap = self.pools.calc_capacities(self.tx_source, self.blockrate)
        self.stablefeerate = self.cap.calc_stablefeerate(rate_ratio_thresh)

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


class SimStats(object):
    def __init__(self, stats, cap, timespent, numiters, stablefeerate):
        self.stats = stats
        self.cap = cap
        self.timespent = timespent
        self.numiters = numiters
        self.stablefeerate = stablefeerate

    def print_stats(self):
        print("Num iters: %d" % self.numiters)
        print("Time spent: %.2f" % self.timespent)
        print("Stable feerate: %d\n" % self.stablefeerate)
        self.cap.print_caps()


class SteadyStateStats(SimStats):
    def __init__(self, *args):
        super(self.__class__, self).__init__(*args)
        self.stats = filter(lambda qc: qc.feerate >= self.stablefeerate,
                            self.stats.stats)

    def print_stats(self):
        super(self.__class__, self).print_stats()
        print("\nFeerate\tAvgwait\tSP\tASB")
        for qc in self.stats:
            print("%d\t%.2f\t%.3f\t%.2f" %
                  (qc.feerate, qc.avgwait, qc.stranded_proportion,
                   qc.avg_strandedblocks))


class TransientStats(SimStats):
    def __init__(self, *args):
        super(self.__class__, self).__init__(*args)
        for twait in self.stats.values():
            twait.calc_stats()

    def print_stats(self):
        super(self.__class__, self).print_stats()
        print("\nFeerate\tAvgwait\tError")
        for feerate, twait in self.stats.items():
            print("%d\t%.2f\t%.2f" %
                  (feerate, twait.mean, twait.mean_interval[1] - twait.mean))
