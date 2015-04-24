from __future__ import division

import multiprocessing
import logging
from time import time
from bisect import bisect_left

from feemodel.simul.simul import cap_ratio_thresh

ITERSCHUNK = 100
PROCESS_COMPLETE = 'process_complete'

logger = logging.getLogger(__name__)


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
                 numprocesses=None, stopflag=None):
    starttime = time()
    if init_entries is None:
        init_entries = {}
    if not feepoints:
        feepoints = _get_default_feepoints(sim.cap, sim.stablefeerate)
    else:
        feepoints = filter(lambda feerate: feerate >= sim.stablefeerate,
                           feepoints)
    if not numprocesses:
        numprocesses = multiprocessing.cpu_count()
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
    logger.debug("Subprocesses started.")

    starttime = time()
    elapsedtime = 0
    waitvectors = []
    while len(waitvectors) < maxiters and (
            len(waitvectors) < miniters or elapsedtime <= maxtime) and (
            stopflag is None or not stopflag.is_set()):
        waitvectors.extend(resultqueue.get())
        elapsedtime = time() - starttime
    process_stopflag.set()
    logger.debug("Subprocesses sent stop signal.")

    num_process_complete = 0
    while num_process_complete < numprocesses:
        res = resultqueue.get()
        if res == PROCESS_COMPLETE:
            num_process_complete += 1
        else:
            waitvectors.extend(res)
    logger.debug("Received PROCESS_COMPLETE from all subprocesses.")

    for process in processes:
        process.join()
    if stopflag and stopflag.is_set():
        raise StopIteration
    logger.debug("Subprocesses joined and completed.")

    waittimes = zip(*waitvectors)
    return feepoints, waittimes, elapsedtime, len(waitvectors)


def transientsim_process(sim, init_entries, feepoints, resultqueue,
                         stopflag):
    waitvectors = []
    try:
        for waitvector in transientsim_core(sim, init_entries, feepoints):
            waitvectors.append(waitvector)
            if stopflag.is_set():
                resultqueue.put(waitvectors)
                resultqueue.put(PROCESS_COMPLETE)
                break
            if len(waitvectors) == ITERSCHUNK:
                resultqueue.put(waitvectors)
                waitvectors = []
    except Exception:
        # Because transientsim_process is called in a child process,
        # we must catch all errors if we want to see a stack trace.
        logger.exception("Exception in transientsim_process.")


def _get_default_feepoints(cap, stablefeerate):
    NUMPOINTS = 20
    cap_ratio_targets = [i/NUMPOINTS*cap_ratio_thresh
                         for i in range(1, NUMPOINTS+1)]
    feepoints = [
        cap.feerates[cap.cap_ratio_index(cap_ratio)]
        for cap_ratio in reversed(cap_ratio_targets)]
    feepoints = sorted(set(feepoints))
    assert feepoints[0] >= stablefeerate
    return feepoints
