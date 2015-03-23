from feemodel.util import DataSample


def transientsim(sim, init_entries=None,
                 miniters=1000, maxiters=10000, maxtime=60):
    feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
    if init_entries is None:
        init_entries = []

    numiters = 0
    simtime = 0.
    stranded = set(feepoints)
    waittimes = {feerate: DataSample() for feerate in stranded}
    for block, realtime in sim.run(init_entries=init_entries):
        simtime += block.interval
        stranding_feerate = block.sfr

        for feerate in list(stranded):
            if feerate >= stranding_feerate:
                waittimes[feerate].add_datapoints([simtime])
                stranded.remove(feerate)

        if not stranded:
            numiters += 1
            if (numiters >= maxiters or
                    numiters >= miniters and realtime > maxtime):
                break
            else:
                simtime = 0.
                stranded = set(feepoints)
                sim.mempool.reset()

    return waittimes, realtime, numiters


def _get_feepoints(cap, stablefeerate):
    '''Choose suitable feerates at which to evaluate stats.'''
    feepoints = list(cap.feerates)
    extrapoints = []
    prevcap = cap.cap_lower[0]
    totalcap = cap.cap_lower[-1]
    for feerate in feepoints:
        # Don't allow too big a jump in cap between feepoints; otherwise
        # linear interpolation of wait times could give poor results.
        currcap = cap.get_cap(feerate)
        if currcap - prevcap > 0.05*totalcap:
            extrapoints.append(feerate-1)
        prevcap = currcap
    feepoints.extend(extrapoints)

    feepoints = sorted(set(feepoints))
    feepoints = filter(lambda feerate: feerate >= stablefeerate, feepoints)
    return feepoints
