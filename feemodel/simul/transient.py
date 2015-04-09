import multiprocessing
from time import time
from collections import defaultdict


def transientsim(sim, feepoints=None, init_entries=None,
                 miniters=1000, maxiters=10000, maxtime=60, stopflag=None):
    '''Transient waittimes simulation.'''
    if init_entries is None:
        init_entries = []
    if not feepoints:
        feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
    numiters = 0
    stranded = set(feepoints)
    waittimes = defaultdict(list)
    starttime = time()
    elapsedrealtime = 0

    for block in sim.run(init_entries=init_entries):
        if stopflag and stopflag.is_set():
            raise StopIteration
        stranding_feerate = block.sfr

        for feerate in list(stranded):
            if feerate >= stranding_feerate:
                waittimes[feerate].append(sim.simtime)
                stranded.remove(feerate)

        if not stranded:
            numiters += 1
            elapsedrealtime = time() - starttime
            if (numiters >= maxiters or
                    numiters >= miniters and elapsedrealtime > maxtime):
                break
            else:
                sim.simtime = 0.
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
    if numprocesses == 1:
        waittimes, _dum, numiters = transientsim(
            sim, feepoints=feepoints, init_entries=init_entries,
            miniters=miniters, maxiters=maxiters, maxtime=maxtime,
            stopflag=stopflag)
    else:
        resultconns = [multiprocessing.Pipe() for i in range(numprocesses)]
        parentconns, childconns = zip(*resultconns)
        maxiterschunk = maxiters // numprocesses
        miniterschunk = miniters // numprocesses
        processes = [
            multiprocessing.Process(
                target=_multiproc_target,
                args=(sim, feepoints, init_entries,
                      miniterschunk, maxiterschunk, maxtime,
                      stopflag, childconns[i])
            )
            for i in range(numprocesses)]
        for process in processes:
            process.start()

        waittimes = defaultdict(list)
        numiters = 0

        for conn in parentconns:
            result = conn.recv()
            if stopflag and stopflag.is_set():
                raise StopIteration
            waittimeschunk, numiterschunk = result
            for feerate, waitsample in waittimeschunk.items():
                waittimes[feerate].extend(waitsample)
            numiters += numiterschunk

        for process in processes:
            process.join()

    return waittimes, time()-starttime, numiters


def _multiproc_target(sim, feepoints, init_entries,
                      miniters, maxiters, maxtime, stopflag, resultconn):
    '''Multiprocessing wrapper for transientsim.'''
    try:
        waittimes, _dum, numiters = transientsim(
            sim,
            feepoints=feepoints,
            init_entries=init_entries,
            miniters=miniters,
            maxiters=maxiters,
            maxtime=maxtime,
            stopflag=stopflag)
    except StopIteration:
        waittimes = None
        numiters = None
    resultconn.send((waittimes, numiters))


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
