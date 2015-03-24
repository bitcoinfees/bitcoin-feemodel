import multiprocessing
from Queue import Empty
from time import time
from feemodel.util import DataSample


def transientsim(sim, feepoints=None, init_entries=None,
                 miniters=1000, maxiters=10000, maxtime=60):
    '''Transient waittimes simulation.'''
    if init_entries is None:
        init_entries = []
    if not feepoints:
        feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
    numiters = 0
    simtime = 0.
    stranded = set(feepoints)
    waittimes = {feerate: DataSample() for feerate in stranded}
    starttime = time()
    elapsedrealtime = None

    for block in sim.run(init_entries=init_entries):
        simtime += block.interval
        stranding_feerate = block.sfr

        for feerate in list(stranded):
            if feerate >= stranding_feerate:
                waittimes[feerate].add_datapoints([simtime])
                stranded.remove(feerate)

        if not stranded:
            numiters += 1
            elapsedrealtime = time() - starttime
            if (numiters >= maxiters or
                    numiters >= miniters and elapsedrealtime > maxtime):
                break
            else:
                simtime = 0.
                stranded = set(feepoints)
                sim.mempool.reset()

    return waittimes, elapsedrealtime, numiters


def transient_multiproc(sim, feepoints=None, init_entries=None,
                        miniters=1000, maxiters=10000, maxtime=60,
                        multiprocess=None, stopflag=None):
    '''Multiprocessing of transientsim.'''
    starttime = time()
    if init_entries is None:
        init_entries = []
    if not feepoints:
        feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
    numprocesses = (
        multiprocess if multiprocess is not None
        else multiprocessing.cpu_count())
    resultqueue = multiprocessing.Queue()
    maxiterschunk = maxiters // numprocesses
    miniterschunk = miniters // numprocesses
    processes = [
        multiprocessing.Process(
            target=_multiproc_target,
            args=(resultqueue, sim, feepoints, init_entries,
                  miniterschunk, maxiterschunk, maxtime)
        )
        for i in range(numprocesses)]
    for process in processes:
        process.start()

    waittimes = {feerate: DataSample() for feerate in feepoints}
    numiters = 0
    while any([process.is_alive() for process in processes]):
        try:
            result = resultqueue.get(timeout=3)
        except Empty:
            pass
        else:
            waittimeschunk, numiterschunk = result
            for feerate, waitsample in waittimes.items():
                waitsample.add_datapoints(waittimeschunk[feerate].datapoints)
            numiters += numiterschunk
        if stopflag is not None and stopflag.is_set():
            for process in processes:
                process.terminate()
            # try:
            #     while True:
            #         resultqueue.get(False)
            # except Empty:
            #     pass
            raise StopIteration

    return waittimes, time()-starttime, numiters


def _multiproc_target(resultqueue, sim, feepoints, init_entries,
                      miniters, maxiters, maxtime):
    '''Multiprocessing wrapper for transientsim.'''
    waittimes, elapsedrealtime, numiters = transientsim(
        sim, feepoints, init_entries, miniters, maxiters, maxtime)
    resultqueue.put((waittimes, numiters))


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
