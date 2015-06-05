from __future__ import division

from tabulate import tabulate

from feemodel.util import Function, DiscreteFunction


class WaitFn(Function):
    '''Wait times as a function of feerate.

    The main difference from the superclass is that:
    for feerate > x[-1], f(feerate) := f(x[-1])

    This is because the wait function has a lower bound (the block interval
    of ~ 600 seconds) and we always want to choose feepoint vector x such that
    f(x[-1]) is close to this bound.
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


# WARNING: This analysis only holds when average tx size << average max block
#          size. Otherwise capacity can be wasted when large transactions
#          consistently fail to fit into blocks. We still need a robust way
#          of determining the stable feerate.
class Capacity(object):

    def __init__(self, pools, txsource):
        self.txbyteratefn = txsource.get_byteratefn()
        self.hashratefn = pools.get_hashratefn()
        self.capfn = pools.get_capacityfn()
        self.procratesfn = None

    def calc_stablefeerate(self, utilization_thresh):
        # Start from the point where tx byterate is zero.
        laststablefeerate, dummy = self.txbyteratefn[-1]
        allocatedbyterate = 0.

        feerates = []
        procbyterates = []

        for feerate, cap in reversed(list(self.capfn)):
            capthresh = cap*utilization_thresh
            txbyterate = self.txbyteratefn(feerate)
            residual_byterate = txbyterate - allocatedbyterate
            if capthresh > residual_byterate:
                laststablefeerate = feerate
                procbyterate = self._allocate_byterate(feerate,
                                                       residual_byterate)
                feerates.append(feerate)
                procbyterates.append(procbyterate)
                allocatedbyterate += procbyterate
                continue
            # Queue is no longer stable.
            for txfeerate, txbyterate in self.txbyteratefn:
                residual_byterate = txbyterate - allocatedbyterate
                if residual_byterate < capthresh:
                    laststablefeerate = min(laststablefeerate, txfeerate)
                    break

        feerates.reverse()
        procbyterates.reverse()
        self.procratesfn = DiscreteFunction(feerates, procbyterates)
        return laststablefeerate

    def _allocate_byterate(self, feerate, txbyterate):
        hashrate = self.hashratefn(feerate)
        next_hashrate = self.hashratefn(feerate - 1)
        hashrate_delta = hashrate - next_hashrate
        hashrate_prop = hashrate_delta / hashrate

        caprate = self.capfn(feerate)
        next_caprate = self.capfn(feerate - 1)
        caprate_delta = caprate - next_caprate
        caprate_prop = caprate_delta / caprate

        prop_min = min(hashrate_prop, caprate_prop)
        return prop_min*txbyterate

    def inv_util(self, utilization_target):
        """Inverse utilization.

        Returns the lowest feerate at which self.txbyteratefn(feerate) /
        self.capfn(feerate) <= utilization_target.
        """
        feerates = sorted(set(self.capfn._x + self.txbyteratefn._x))
        for feerate in feerates:
            try:
                utilization = self.txbyteratefn(feerate) / self.capfn(feerate)
            except ZeroDivisionError:
                continue
            if utilization <= utilization_target:
                return feerate

        # This won't normally happen, because we ensure txbyteratefn extends
        # to 0 byterate, and cap is nonzero as feerate -> inf.
        raise ValueError("utilization_target must be > 0.")


# TODO: Deprecate this.
class CapacityRatios(Function):

    def __init__(self, pools, txsource):
        raise NotImplementedError
        self.capfn = pools.get_capacityfn()
        self.txbyteratefn = txsource.get_byteratefn()
        feerates = sorted(set(self.capfn._x + self.txbyteratefn._x))
        cap_ratios = [
            self.txbyteratefn(feerate) / self.capfn(feerate)
            if self.capfn(feerate) else float("inf")
            for feerate in feerates
        ]
        if cap_ratios[-1] > 0:
            # Ensure that the last cap_ratio is always zero.
            feerates.append(feerates[-1] + 1)
            cap_ratios.append(0)
        super(CapacityRatios, self).__init__(feerates, cap_ratios)

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
        raise ValueError("cap_ratio_target must be > 0.")
