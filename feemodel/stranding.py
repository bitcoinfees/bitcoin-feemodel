'''Stranding fee rate calculation

This module contains functions for calculating the stranding fee rate.
'''
from __future__ import division

import multiprocessing
from random import random
from feemodel.config import minrelaytxfee
from feemodel.util import DataSample

__all__ = ['tx_preprocess', 'calc_stranding_feerate']


def tx_preprocess(memblock):
    '''Preprocess MemBlock transactions for calculating stranding fee rate.

    Returns a list of transactions represented by (feerate, inblock), which
    fulfil certain criteria.

    Arguments:
        memblock - A MemBlock object
    '''
    min_leadtime = _calc_min_leadtime(memblock)

    txs = [
        (entry.feerate, entry.inblock)
        for entry in memblock.entries.itervalues()
        if _deps_check(entry, memblock.entries) and
        entry.leadtime >= min_leadtime and
        not entry.isconflict and
        not entry.is_high_priority()]

    return txs


def calc_stranding_feerate(txs, bootstrap=True, multiprocess=None):
    '''Compute stranding feerate from preprocessed txs.

    txs is [(feerate, inblock) for some list of txs].
    bootstrap specifies whether or not to compute error estimates using
    bootstrap resampling.

    multiprocess is the number of processes to use. defaults to
    multiprocessing.cpu_count().
    '''
    if not len(txs):
        raise ValueError('Empty txs list')

    txs.sort(key=lambda x: x[0], reverse=True)
    sfr = _calc_stranding_single(txs)
    abovek = aboven = belowk = belown = 0
    alt_bias_ref = None
    for tx in txs:
        if tx[0] >= sfr:
            abovek += tx[1]
            aboven += 1
        else:
            if alt_bias_ref is None:
                alt_bias_ref = tx[0]
            belowk += not tx[1]
            belown += 1
    if alt_bias_ref is None:
        alt_bias_ref = minrelaytxfee

    if bootstrap and sfr != float("inf"):
        N = 1000  # Number of bootstrap estimates
        numprocesses = (
            multiprocess if multiprocess is not None
            else multiprocessing.cpu_count())
        if numprocesses == 1:
            bs_estimates = [_calc_stranding_single(bootstrap_sample(txs))
                            for i in range(N)]
        else:
            workers = multiprocessing.Pool(processes=numprocesses)
            Nchunk = N // numprocesses
            result = workers.map_async(
                processwork, [(txs, Nchunk)]*numprocesses)
            bs_estimates = sum(result.get(), [])
            workers.terminate()

        if not any([b == float("inf") for b in bs_estimates]):
            datasample = DataSample(bs_estimates)
            datasample.calc_stats()
            mean = datasample.mean
            std = datasample.std
            bias = mean - alt_bias_ref
            alt_bias = mean - sfr
            if abs(alt_bias) > abs(bias):
                bias = alt_bias
        else:
            bias = std = mean = float("inf")
    else:
        bias = std = mean = float("inf")

    return {"sfr": sfr, "bias": bias, "mean": mean, "std": std,
            "abovekn": (abovek, aboven), "belowkn": (belowk, belown)}


def processwork(args):
    '''Target function of the process pool.'''
    txs, N = args
    return [_calc_stranding_single(bootstrap_sample(txs)) for i in range(N)]


def _calc_stranding_single(txs):
    '''Compute stranding feerate for a single sample.

    This is called by calc_stranding_feerate once for each iteration
    in the bootstrap resampling estimation.

    txs is assumed reverse sorted by feerate.
    '''
    sfr = float("inf")
    cumk = 0
    maxk = 0
    maxidx = len(txs) - 1

    for idx, tx in enumerate(txs):
        cumk += 1 if tx[1] else -1
        if idx < maxidx and txs[idx+1][0] == tx[0]:
            continue
        if cumk > maxk:
            maxk = cumk
            sfr = tx[0]

    return sfr


def bootstrap_sample(txs):
    '''Bootstrap resampling of txs.'''
    n = len(txs)
    sample = [txs[int(random()*n)] for idx in range(n)]
    sample.sort(key=lambda x: x[0], reverse=True)
    return sample


def _deps_check(entry, entries):
    deps = [entries.get(dep_id) for dep_id in entry.depends]
    return all([dep.inblock if dep else True for dep in deps])


def _calc_min_leadtime(memblock):
    '''Calc the min leadtime of a memblock.'''
    try:
        min_leadtime = min([
            entry.leadtime
            for entry in memblock.entries.itervalues()
            if entry.inblock])
    except ValueError:
        # No memblock entries are inblock
        min_leadtime = 0
    return min_leadtime
