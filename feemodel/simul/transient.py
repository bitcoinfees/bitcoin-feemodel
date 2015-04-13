from __future__ import division

import multiprocessing
from time import time
from bisect import bisect_left

from feemodel.simul.simul import cap_ratio_thresh

ITERSCHUNK = 100
PROCESS_COMPLETE = 'process_complete'


def transientsim_core(sim, init_entries, feepoints):
    """Transient wait time generator.

    Each iteration yields one realization of the wait time random vector.
    feepoints must not include any feerates lower than sim.stablefeerate,
    otherwise the simulation could be unstable.
    """
    waittimes = [None]*len(feepoints)
    min_sfr_idx = len(feepoints)
    for block in sim.run(init_entries=init_entries):
        sfr_idx = bisect_left(feepoints, block.sfr)
        for i in range(sfr_idx, min_sfr_idx):
            waittimes[i] = sim.simtime
        if sfr_idx == 0:
            yield waittimes[:]
            min_sfr_idx = len(feepoints)
            sim.simtime = 0
            sim.mempool.reset()
        else:
            min_sfr_idx = min(sfr_idx, min_sfr_idx)


def transientsim(sim, feepoints=None, init_entries=None,
                 miniters=1000, maxiters=10000, maxtime=60,
                 multiprocess=None, stopflag=None):
    starttime = time()
    if init_entries is None:
        init_entries = {}
    if not feepoints:
        feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
    else:
        feepoints = filter(lambda feerate: feerate >= sim.stablefeerate,
                           feepoints)

    numprocesses = (
        multiprocess if multiprocess is not None
        else multiprocessing.cpu_count())
    resultqueue = multiprocessing.Queue()
    process_stopflag = multiprocessing.Event()
    processes = [
        multiprocessing.Process(
            target=transientsim_process,
            args=(sim, init_entries, feepoints, resultqueue, process_stopflag)
        )
        for i in range(numprocesses)
    ]
    for process in processes:
        process.start()

    starttime = time()
    elapsedtime = 0
    waitvectors = []
    while len(waitvectors) < maxiters and (
            len(waitvectors) < miniters or elapsedtime <= maxtime) and (
            stopflag is None or not stopflag.is_set()):
        waitvectors.extend(resultqueue.get())
        elapsedtime = time() - starttime
    process_stopflag.set()

    num_process_complete = 0
    while num_process_complete < numprocesses:
        res = resultqueue.get()
        if res == PROCESS_COMPLETE:
            num_process_complete += 1
        else:
            waitvectors.extend(res)

    for process in processes:
        process.join()
    if stopflag and stopflag.is_set():
        raise StopIteration

    waittimes = zip(*waitvectors)
    return feepoints, waittimes, elapsedtime, len(waitvectors)


def transientsim_process(sim, init_entries, feepoints, resultqueue,
                         stopflag):
    waitvectors = []
    for waitvector in transientsim_core(sim, init_entries, feepoints):
        waitvectors.append(waitvector)
        if stopflag.is_set():
            resultqueue.put(waitvectors)
            resultqueue.put(PROCESS_COMPLETE)
            break
        if len(waitvectors) == ITERSCHUNK:
            resultqueue.put(waitvectors)
            waitvectors = []


def _get_feepoints(cap, stablefeerate):
    '''Choose suitable feerates at which to evaluate stats.'''
    NUMPOINTS = 20
    cap_ratio_targets = [i/NUMPOINTS*cap_ratio_thresh
                         for i in range(1, NUMPOINTS+1)]
    feepoints = [
        cap.feerates[cap.cap_ratio_index(cap_ratio)]
        for cap_ratio in reversed(cap_ratio_targets)]
    feepoints = sorted(set(feepoints))
    assert feepoints[0] >= stablefeerate
    return feepoints


# #def transientsim(sim, feepoints=None, init_entries=None,
# #                 miniters=1000, maxiters=10000, maxtime=60, stopflag=None):
# #    '''Transient waittimes simulation.'''
# #    if init_entries is None:
# #        init_entries = []
# #    if not feepoints:
# #        feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
# #    numiters = 0
# #    stranded = set(feepoints)
# #    waittimes = defaultdict(list)
# #    starttime = time()
# #    elapsedrealtime = 0
# #
# #    for block in sim.run(init_entries=init_entries):
# #        if stopflag and stopflag.is_set():
# #            raise StopIteration
# #        stranding_feerate = block.sfr
# #
# #        for feerate in list(stranded):
# #            if feerate >= stranding_feerate:
# #                waittimes[feerate].append(sim.simtime)
# #                stranded.remove(feerate)
# #
# #        if not stranded:
# #            numiters += 1
# #            elapsedrealtime = time() - starttime
# #            if (numiters >= maxiters or
# #                    numiters >= miniters and elapsedrealtime > maxtime):
# #                break
# #            else:
# #                sim.simtime = 0.
# #                stranded = set(feepoints)
# #                sim.mempool.reset()
# #
# #    return waittimes, elapsedrealtime, numiters
# #
# #
# #def transient_multiproc(sim, feepoints=None, init_entries=None,
# #                        miniters=1000, maxiters=10000, maxtime=60,
# #                        multiprocess=None, stopflag=None):
# #    '''Multiprocessing of transientsim.'''
# #    starttime = time()
# #    if init_entries is None:
# #        init_entries = []
# #    if not feepoints:
# #        feepoints = _get_feepoints(sim.cap, sim.stablefeerate)
# #    numprocesses = (
# #        multiprocess if multiprocess is not None
# #        else multiprocessing.cpu_count())
# #    if numprocesses == 1:
# #        waittimes, _dum, numiters = transientsim(
# #            sim, feepoints=feepoints, init_entries=init_entries,
# #            miniters=miniters, maxiters=maxiters, maxtime=maxtime,
# #            stopflag=stopflag)
# #    else:
# #        resultconns = [multiprocessing.Pipe() for i in range(numprocesses)]
# #        parentconns, childconns = zip(*resultconns)
# #        maxiterschunk = maxiters // numprocesses
# #        miniterschunk = miniters // numprocesses
# #        processes = [
# #            multiprocessing.Process(
# #                target=_multiproc_target,
# #                args=(sim, feepoints, init_entries,
# #                      miniterschunk, maxiterschunk, maxtime,
# #                      stopflag, childconns[i])
# #            )
# #            for i in range(numprocesses)]
# #        for process in processes:
# #            process.start()
# #
# #        waittimes = defaultdict(list)
# #        numiters = 0
# #
# #        for conn in parentconns:
# #            result = conn.recv()
# #            if stopflag and stopflag.is_set():
# #                raise StopIteration
# #            waittimeschunk, numiterschunk = result
# #            for feerate, waitsample in waittimeschunk.items():
# #                waittimes[feerate].extend(waitsample)
# #            numiters += numiterschunk
# #
# #        for process in processes:
# #            process.join()
# #
# #    return waittimes, time()-starttime, numiters
# #
# #
# #def _multiproc_target(sim, feepoints, init_entries,
# #                      miniters, maxiters, maxtime, stopflag, resultconn):
# #    '''Multiprocessing wrapper for transientsim.'''
# #    try:
# #        waittimes, _dum, numiters = transientsim(
# #            sim,
# #            feepoints=feepoints,
# #            init_entries=init_entries,
# #            miniters=miniters,
# #            maxiters=maxiters,
# #            maxtime=maxtime,
# #            stopflag=stopflag)
# #    except StopIteration:
# #        waittimes = None
# #        numiters = None
# #    resultconn.send((waittimes, numiters))
