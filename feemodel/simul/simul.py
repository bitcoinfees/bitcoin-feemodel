from collections import defaultdict
from bisect import insort, bisect_left
import threading

from feemodel.queuestats import QueueStats
from feemodel.simul.txsources import SimTx
from feemodel.util import DataSample, itertimer

rate_ratio_thresh = 0.9


def transient(mempool, pools, tx_source,
              maxiters=10000, maxtime=60, feeclasses=None):
    stopflag = threading.Event()
    sim = Simul(pools, tx_source)
    if not feeclasses:
        feeclasses = _get_feeclasses(sim.cap)
    else:
        feeclasses.sort()
    feeclasses = filter(lambda fee: fee >= sim.stablefeerate, feeclasses)
    tstats = {feerate: DataSample() for feerate in feeclasses}

    def callback(sim):
        callback.totaltime += sim.lastblock['blockinterval']
        stranding_feerate = sim.lastblock['sfr']
        sidx = bisect_left(callback.stranded, stranding_feerate)

        for feerate in callback.stranded[sidx:]:
            tstats[feerate].add_datapoints([callback.totaltime])
        callback.stranded = callback.stranded[:sidx]

        if not callback.stranded:
            callback.numiters += 1
            if callback.numiters == maxiters:
                stopflag.set()
            else:
                callback.totaltime = 0.
                callback.stranded = feeclasses[:]
                sim.mempool.reset()

    callback.totaltime = 0.
    callback.numiters = 0
    callback.stranded = feeclasses[:]
    sim.run(callback, mempool=mempool, maxtime=maxtime,
            maxiters=float("inf"), stopflag=stopflag)

    return TransientStats(tstats, sim.cap, sim.elapsedtime,
                          callback.numiters, sim.stablefeerate)


def steadystate(pools, tx_source,
                maxiters=100000, maxtime=600, feeclasses=None):
    sim = Simul(pools, tx_source)
    if not feeclasses:
        feeclasses = _get_feeclasses(sim.cap)
    else:
        feeclasses.sort()
    feeclasses = filter(lambda fee: fee >= sim.stablefeerate, feeclasses)
    qstats = QueueStats(feeclasses)

    def callback(sim):
        qstats.next_block(sim.i, sim.lastblock['blockinterval'],
                          sim.lastblock['sfr'])

    sim.run(callback, maxiters=maxiters, maxtime=maxtime)
    return SteadyStateStats(qstats, sim.cap, sim.elapsedtime,
                            sim.i + 1, sim.stablefeerate)


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
        self.lastblock = {
            'sfr': None, 'blocksize': None, 'blockinterval': None,
            'is_blocksizeltd': None, 'poolname': None}
        self.i = None
        self.elapsedtime = None

    def run(self, callback=None, mempool=None, maxiters=100000,
            maxtime=60, stopflag=None):
        if callback is None:
            callback = lambda x: None
        if mempool is None:
            mempool = {}
        self.mempool = SimMempool(mempool)
        for self.i, self.elapsedtime in itertimer(maxiters, maxtime, stopflag):
            blockint, name, maxblocksize, minfeerate = self.pools.next_block()
            newtxs = self.tx_source.generate_txs(blockint)
            newtxs = filter(lambda tx: tx.feerate >= self.stablefeerate,
                            newtxs)
            self.mempool._add_txs(newtxs)
            blockstat = self.mempool._process_block(maxblocksize, minfeerate)

            self.lastblock['sfr'] = max(blockstat[0], self.stablefeerate)
            self.lastblock['blocksize'] = blockstat[1]
            self.lastblock['is_blocksizeltd'] = blockstat[2]
            self.lastblock['txs'] = blockstat[3]
            self.lastblock['poolname'] = name
            self.lastblock['blockinterval'] = blockint

            callback(self)


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

    def _process_block(self, maxblocksize, minfeerate):
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

        sfr = sfr if blocksize_ltd else minfeerate
        blocksize = blocksize
        is_blocksizeltd = bool(blocksize_ltd)

        return sfr, blocksize, is_blocksizeltd, blocktxs

    def _calc_size(self):
        numbytes = (sum([tx.size for tx in self._tx_nodeps]) +
                    sum([tx.size for tx in self._tx_havedeps.values()]))
        numtxs = len(self._tx_nodeps) + len(self._tx_havedeps)
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
        for feerate, twait in self.stats.items():
            if twait.n > 1:
                twait.calc_stats()
            else:
                del self.stats[feerate]

    def print_stats(self):
        super(self.__class__, self).print_stats()
        sitems = sorted(self.stats.items())
        print("\nFeerate\tAvgwait\tError")
        for feerate, twait in sitems:
            print("%d\t%.2f\t%.2f" %
                  (feerate, twait.mean, twait.mean_interval[1] - twait.mean))


def _get_feeclasses(cap):
    feerates = cap.feerates[1:]
    caps = cap.caps
    capsdiff = [caps[idx] - caps[idx-1]
                for idx in range(1, len(feerates)+1)]
    feeDS = DataSample(feerates)
    feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
                  for p in range(5, 100, 5)]
    feeclasses = sorted(set(feeclasses))
    return feeclasses
