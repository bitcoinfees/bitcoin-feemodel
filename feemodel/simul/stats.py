from __future__ import division

from tabulate import tabulate

from feemodel.util import Function


class WaitFn(Function):
    '''Wait times as a function of feerate.

    The main difference from the superclass is that:
    for feerate > x[-1], f(feerate) := f(x[-1])

    This is because the wait function has a lower bound (the block interval
    of ~ 600 seconds) and we always choose x such that f(x[-1]) is close
    to this bound.
    '''

    def __init__(self, feerates, waits, errors=None):
        super(WaitFn, self).__init__(feerates, waits)
        self.errors = errors

    @property
    def feerates(self):
        return self._x

    @property
    def waits(self):
        return self._y

    def __call__(self, feerate):
        '''Linear interpolation of wait time at feerate.

        If feerate is lower than all available feerate datapoints,
        returns None. If it is larger, return the boundary value.
        '''
        return super(WaitFn, self).__call__(feerate, use_upper=True)

    def inv(self, wait):
        '''Return the feerate for a specified wait time.

        If wait is smaller than all available wait datapoints, returns None.
        If larger, return the boundary value of the function.
        '''
        return super(WaitFn, self).inv(wait, use_upper=True)

    def __str__(self):
        headers = ['Feerate', 'Wait', 'Error']
        errors = map(lambda err: err if err else '-', self.errors)
        table = zip(self.feerates, self.waits, errors)
        return tabulate(table, headers=headers)


class CapacityRatios(Function):

    def __init__(self, pools, txsource):
        super(CapacityRatios, self).__init__([], [])
        self.capfn = pools.get_capacityfn()
        self.txbyteratefn = txsource.get_byteratefn()
        feerates = sorted(set(self.capfn._x + self.txbyteratefn._x))
        for feerate in feerates:
            try:
                cap_ratio = self.txbyteratefn(feerate) / self.capfn(feerate)
            except ZeroDivisionError:
                cap_ratio = float("inf")
            # self.cap_ratios.append(cap_ratio)
            self.addpoint(feerate, cap_ratio)

        lastfeerate, lastratio = self[-1]
        if lastratio:
            # Ensure that the last cap_ratio is always zero.
            self.addpoint(lastfeerate+1, 0)

    def calc_stablefeerate(self, cap_ratio_thresh):
        return self.inv(cap_ratio_thresh)

    def __call__(self, feerate):
        if feerate not in self._x:
            raise ValueError("Not defined at this feerate")
        return super(CapacityRatios, self).__call__(feerate)

    def inv(self, cap_ratio_target):
        """Get the lowest feerate such that:

        self(feerate) <= cap_ratio_target
        """
        for feerate, cap_ratio in iter(self):
            if cap_ratio <= cap_ratio_target:
                return feerate
        # Because we ensure in init that min(self.cap_ratios) = 0
        raise AssertionError("This is not supposed to happen.")

    # TODO: deprecate
    def cap_ratio_index(self, cap_ratio_target):
        """Cap ratio index.

        Get the lowest index such that
        self.cap_ratio[index] <= cap_ratio_target.
        """
        raise NotImplementedError
        for idx, cap_ratio in enumerate(self.cap_ratios):
            if cap_ratio <= cap_ratio_target:
                return idx
        # Because we ensure in init that min(self.cap_ratios) = 0
        raise AssertionError("This is not supposed to happen.")

    # TODO: deprecate
    def print_cap(self, numpoints=20):
        raise NotImplementedError
        stats = self.get_stats(numpoints)
        headers = ["Feerate", "TxByterate", "Cap"]
        table = zip(
            stats['feerates'],
            stats['txbyterates'],
            stats['caps']
        )
        print(tabulate(table, headers=headers))


# TODO: deprecate this.
def get_feeclasses(cap, stablefeerate):
    '''Choose suitable feerates at which to evaluate stats.'''
    raise NotImplementedError
    # quantize = 200
    # feeclasses = [int(ceil((feerate + 1) / quantize)*quantize)
    #               for feerate in cap.feerates]
    # feeclasses = sorted(set(feeclasses))
    # feeclasses = filter(lambda feerate: feerate >= stablefeerate, feeclasses)
    # return feeclasses


# #class SimStats(object):
# #    def __init__(self):
# #        self.timestamp = 0.
# #        self.timespent = None
# #        self.numiters = None
# #        self.cap = None
# #        self.stablefeerate = None
# #
# #    def print_stats(self):
# #        if self:
# #            name = self.__class__.__name__
# #            print(("{}\n" + "="*len(name)).format(name))
# #            print("Num iters: %d" % self.numiters)
# #            print("Time spent: %.2f" % self.timespent)
# #            print("Stable feerate: %d" % self.stablefeerate)
# #            self.cap.print_cap()
# #
# #    def get_stats(self):
# #        if not self:
# #            return None
# #        return {
# #            'timestamp': self.timestamp,
# #            'timespent': self.timespent,
# #            'numiters': self.numiters,
# #            'cap': self.cap.__dict__,
# #            'stablefeerate': self.stablefeerate}
# #
# #    def __nonzero__(self):
# #        return bool(self.timestamp)

# #def get_feeclasses(cap, tx_source, stablefeerate):
# #    '''Choose suitable feerates at which to evaluate stats.'''
# #    feerates = cap.feerates[1:]
# #    caps = cap.cap_lower
# #    capsdiff = [caps[idx] - caps[idx-1]
# #                for idx in range(1, len(feerates)+1)]
# #    feeDS = DataSample(feerates)
# #    feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
# #                  for p in range(5, 100, 5)]
# #    # Round up to nearest 200 satoshis
# #    quantize = 200
# #    feeclasses = [int(ceil((feerate + 1) / quantize)*quantize)
# #                  for feerate in feeclasses]
# #    feeclasses = sorted(set(feeclasses))
# #
# #    new_feeclasses = [True]
# #    while new_feeclasses:
# #        byterates = tx_source.get_byterates(feeclasses)
# #        # The byterate in each feeclass should not exceed 0.1 of the total
# #        byteratethresh = 0.1 * byterates[0]
# #        new_feeclasses = []
# #        for idx in range(len(byterates)-1):
# #            byteratediff = byterates[idx] - byterates[idx+1]
# #            if byteratediff > byteratethresh:
# #                feegap = feeclasses[idx+1] - feeclasses[idx]
# #                if feegap > 1:
# #                    new_feeclasses.append(feeclasses[idx] + int(feegap/2))
# #        if byterates[-1] > byteratethresh:
# #            new_feeclasses.append(feeclasses[-1]*2)
# #        feeclasses.extend(new_feeclasses)
# #        feeclasses.sort()
# #
# #    feeclasses = filter(lambda fee: fee >= stablefeerate, feeclasses)
# #
# #    return feeclasses


# #def _get_feeclasses(cap):
# #    feerates = cap.feerates[1:]
# #    caps = cap.caps
# #    capsdiff = [caps[idx] - caps[idx-1]
# #                for idx in range(1, len(feerates)+1)]
# #    feeDS = DataSample(feerates)
# #    feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
# #                  for p in range(5, 100, 5)]
# #    feeclasses = sorted(set(feeclasses))
# #    return feeclasses


# #def transient(mempool, pools, tx_source,
# #              maxiters=10000, maxtime=60, feeclasses=None):
# #    sim = Simul(pools, tx_source)
# #    if not feeclasses:
# #        feeclasses = _get_feeclasses(sim.cap)
# #    else:
# #        feeclasses.sort()
# #    feeclasses = filter(lambda fee: fee >= sim.stablefeerate, feeclasses)
# #    tstats = {feerate: DataSample() for feerate in feeclasses}
# #
# #    simtime = 0.
# #    stranded = set(feeclasses)
# #    numiters = 0
# #    for block, realtime in sim.run(mempool=mempool, maxiters=float("inf"),
# #                                   maxtime=maxtime):
# #        simtime += block.interval
# #        stranding_feerate = block.sfr
# #
# #        for feerate in list(stranded):
# #            if feerate >= stranding_feerate:
# #                tstats[feerate].add_datapoints([simtime])
# #                stranded.remove(feerate)
# #
# #        if not stranded:
# #            numiters += 1
# #            if numiters >= maxiters:
# #                break
# #            else:
# #                simtime = 0.
# #                stranded = set(feeclasses)
# #                sim.mempool.reset()
# #
# #    return TransientStats(tstats, sim.cap, realtime, numiters,
# #                          sim.stablefeerate)
# #
# #
# #def steadystate(pools, tx_source,
# #                maxiters=100000, maxtime=600, feeclasses=None):
# #    sim = Simul(pools, tx_source)
# #    if not feeclasses:
# #        feeclasses = _get_feeclasses(sim.cap)
# #    else:
# #        feeclasses.sort()
# #    feeclasses = filter(lambda fee: fee >= sim.stablefeerate, feeclasses)
# #    qstats = QueueStats(feeclasses)
# #
# #    for block, realtime in sim.run(maxiters=maxiters, maxtime=maxtime):
# #        qstats.next_block(block.height, block.interval, block.sfr)
# #
# #    return SteadyStateStats(qstats, sim.cap, realtime,
# #                            block.height+1, sim.stablefeerate)
# #
# #
# #class SteadyStateStats(SimStats):
# #    def __init__(self, *args):
# #        super(self.__class__, self).__init__(*args)
# #        self.stats = filter(lambda qc: qc.feerate >= self.stablefeerate,
# #                            self.stats.stats)
# #
# #    def print_stats(self):
# #        super(self.__class__, self).print_stats()
# #        table = Table()
# #        table.add_row(('Feerate', 'Avgwait', 'SP', 'ASB'))
# #        for qc in self.stats:
# #            table.add_row((
# #                qc.feerate,
# #                '%.2f' % qc.avgwait,
# #                '%.2f' % qc.stranded_proportion,
# #                '%.2f' % qc.avg_strandedblocks,
# #            ))
# #        table.print_table()
# #
# #
# #class TransientStats(SimStats):
# #    def __init__(self, *args):
# #        super(self.__class__, self).__init__(*args)
# #        for feerate, twait in self.stats.items():
# #            if twait.n > 1:
# #                twait.calc_stats()
# #            else:
# #                del self.stats[feerate]
# #
# #    def print_stats(self):
# #        super(self.__class__, self).print_stats()
# #        sitems = sorted(self.stats.items())
# #        table = Table()
# #        table.add_row(('Feerate', 'Avgwait', 'Error'))
# #        for feerate, twait in sitems:
# #            table.add_row((
# #                feerate,
# #                '%.2f' % twait.mean,
# #                '%.2f' % (twait.mean_interval[1] - twait.mean)
# #            ))
# #        table.print_table()


# #class Capacity(object):
# #    def __init__(self, pools, tx_source):
# #        self.pfeerates, self.pcap_lower, self.pcap_upper =
# #                pools.get_capacity()
# #        _d, pools_txbyterates = (
# #            tx_source.get_byterates(feerates=self.pfeerates))
# #        feerates, txbyterates = tx_source.get_byterates()
# #        byteratemap = {}
# #        for feerate, byterate in zip(self.pfeerates, pools_txbyterates):
# #            byteratemap[feerate] = byterate
# #        for feerate, byterate in zip(feerates, txbyterates):
# #            byteratemap[feerate] = byterate
# #
# #        self.feerates, self.tx_byterates = zip(*sorted(byteratemap.items()))
# #        self.cap_lower = [self.get_cap(feerate)
# #                          for feerate in self.feerates]
# #        self.cap_upper = [self.get_cap(feerate, upper=True)
# #                          for feerate in self.feerates]
# #
# #    def get_cap(self, feerate, upper=False):
# #        '''Get capacity for a specified feerate.'''
# #        if feerate < 0:
# #            return 0
# #        if upper:
# #            return self.pcap_upper[bisect(self.pfeerates, feerate)-1]
# #        else:
# #            return self.pcap_lower[bisect(self.pfeerates, feerate)-1]
# #
# #    def calc_stablefeerate(self, rate_ratio_thresh):
# #        stablefeerate = None
# #        for idx in range(len(self.feerates)):
# #            if not self.cap_lower[idx]:
# #                continue
# #            rate_ratio = self.tx_byterates[idx] / self.cap_lower[idx]
# #            if rate_ratio <= rate_ratio_thresh:
# #                stablefeerate = self.feerates[idx]
# #                break
# #        return stablefeerate
# #
# #    def print_cap(self):
# #        table = Table()
# #        table.add_row(("Feerate", "TxByteRate",
# #                       "Cap (lower)", "Cap (upper)"))
# #        for idx in range(len(self.feerates)):
# #            table.add_row((
# #                self.feerates[idx],
# #                '%.2f' % self.tx_byterates[idx],
# #                '%.2f' % self.cap_lower[idx],
# #                '%.2f' % self.cap_upper[idx]))
# #        table.print_table()
