from __future__ import division

import threading
import multiprocessing
import logging
from time import time
from bisect import bisect_left

from feemodel.util import logexceptions

ITERSCHUNK = 100
PROCESS_COMPLETE = 'process_complete'

logger = logging.getLogger(__name__)


def transientsim_core(sim, init_entries, feepoints):
    """Transient wait time generator.

    Each iteration yields one realization of the wait time random vector.
    feepoints should be sorted, and should not include any feerates lower than
    sim.stablefeerate.
    """
    if min(feepoints) < sim.stablefeerate:
        raise ValueError("All feepoints must be >= sim.stablefeerate.")
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
    """A multiprocessing wrapper for transientsim_core."""
    starttime = time()
    if init_entries is None:
        init_entries = {}
    if not feepoints:
        feepoints = get_default_feepoints(sim)
    else:
        feepoints = filter(lambda feerate: feerate >= sim.stablefeerate,
                           sorted(set(feepoints)))
        if not feepoints:
            raise ValueError("No feepoints >= stablefeerate.")
    if numprocesses is None:
        numprocesses = multiprocessing.cpu_count()

    resultqueue = multiprocessing.Queue()
    process_stopflag = multiprocessing.Event()
    target = transientsim_process
    args = (sim, init_entries, feepoints, resultqueue, process_stopflag)
    if numprocesses > 1:
        processes = [multiprocessing.Process(target=target, args=args)
                     for i in range(numprocesses)]
    else:
        # Use a thread instead
        processes = [threading.Thread(target=target, args=args)]
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
    return feepoints, waittimes


@logexceptions
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


def get_default_feepoints(sim, numpoints=20):
    """Returns a list of sensible default feepoints.

    This is basically equispaced feerates from stablefeerate to 0.05
    utilization.
    """
    minfeepoint = sim.stablefeerate
    maxfeepoint = sim.cap.inv_util(0.05)
    assert maxfeepoint >= minfeepoint
    spacing = int((maxfeepoint - minfeepoint) / numpoints)
    if spacing == 0:
        return [maxfeepoint]
    return range(minfeepoint, maxfeepoint+spacing, spacing)
