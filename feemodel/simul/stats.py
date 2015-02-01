from feemodel.simul import Simul
from feemodel.util import DataSample, Table
from feemodel.queuestats import QueueStats

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

    for block, realtime in sim.run(maxiters=maxiters, maxtime=maxtime):
        qstats.next_block(block.height, block.interval, block.sfr)

    return SteadyStateStats(qstats, sim.cap, realtime,
                            block.height+1, sim.stablefeerate)


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
        table = Table()
        table.add_row(('Feerate', 'Avgwait', 'SP', 'ASB'))
        for qc in self.stats:
            table.add_row((
                qc.feerate,
                '%.2f' % qc.avgwait,
                '%.2f' % qc.stranded_proportion,
                '%.2f' % qc.avg_strandedblocks,
            ))
        table.print_table()


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
