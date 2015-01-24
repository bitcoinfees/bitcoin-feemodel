'''Stranding fee rate calculation

This module contains functions for calculating the stranding fee rate.
'''

from random import random, choice

__all__ = ['tx_preprocess', 'calc_stranding_feerate']


def tx_preprocess(memblock, remove_high_priority=False, remove_depped=False,
                  remove_zero_fee=True):
    '''Preprocess MemBlock transactions for calculating stranding fee rate.

    Returns a list of transactions represented by (feerate, inblock), which
    fulfil certain criteria.

    Arguments:
        memblock - A MemBlock object
        remove_high_priority - remove all transactions whose currentpriority
                               is >= 57.6e6 (the threshold defined by Bitcoin
                               Core)
        remove_depped - remove all transactions which depend on other txs
                        in the mempool.
        remove_zero_fee - remove all transactions which have zero fee
    '''
    try:
        min_leadtime = min([entry['leadtime']
                            for entry in memblock.entries.itervalues()
                            if entry['inblock']])
    except ValueError:
        # No memblock entries are inblock
        min_leadtime = 0

    txs = [
        (entry['feerate'], entry['inblock'])
        for entry in memblock.entries.itervalues()
        if _deps_check(entry, memblock.entries, remove_depped) and
        entry['leadtime'] >= min_leadtime and
        not entry.get('isconflict') and
        (entry['feerate'] if remove_zero_fee else True) and
        (entry['currentpriority'] < 57.6e6 if remove_high_priority else True)]

    return txs


def calc_stranding_feerate(txs, bootstrap=True):
    '''Compute stranding feerate from preprocessed txs.

    txs is [(feerate, inblock) for some list of txs].
    bootstrap specifies whether or not to compute error estimates using
    bootstrap resampling.
    '''
    if not len(txs):
        raise ValueError('Empty txs list')

    txs.sort(key=lambda x: x[0], reverse=True)
    sfr = _calc_stranding_single(txs)
    sidx = 0

    try:
        while txs[sidx][0] >= sfr:
            sidx += 1
    except IndexError:
        pass

    abovek = sum(txs[idx][1] for idx in xrange(sidx))
    belowk = sum(not txs[idx][1] for idx in xrange(sidx, len(txs)))

    aboven = sidx
    belown = len(txs) - sidx

    if bootstrap and sfr != float("inf"):
        try:
            alt_bias_ref = txs[sidx][0]
        except IndexError:
            alt_bias_ref = 0

        bs_estimates = [_calc_stranding_single(bootstrap_sample(txs))
                        for i in range(1000)]
        if not any([b == float("inf") for b in bs_estimates]):
            mean = float(sum(bs_estimates)) / len(bs_estimates)
            std = (sum([(b-mean)**2 for b in bs_estimates]) /
                   (len(bs_estimates)-1))**0.5
            bias_ref = max(
                (sfr, abs(mean-sfr)),
                (alt_bias_ref, abs(mean-alt_bias_ref)),
                key=lambda x: x[1])[0]
            bias = mean - bias_ref
        else:
            bias = std = mean = float("inf")
    else:
        bias = std = mean = float("inf")

    return {"sfr": sfr, "bias": bias, "mean": mean, "std": std,
            "abovekn": (abovek, aboven), "belowkn": (belowk, belown)}


def _calc_stranding_single(txs):
    '''Compute stranding feerate for a single sample.

    This is called by calc_stranding_feerate once for each iteration
    in the bootstrap resampling estimation.

    txs is assumed reverse sorted by feerate.
    '''
    cumk = 0
    maxk = 0
    maxidx = 0
    txs.insert(0, (float("inf"), 1))

    for idx, tx in enumerate(txs):
        cumk += 1 if tx[1] else -1
        try:
            if txs[idx+1][0] == tx[0]:
                continue
        except IndexError:
            pass
        if cumk > maxk:
            maxk = cumk
            maxidx = idx

    sfr = txs[maxidx][0]
    txs.pop(0)
    return sfr


def bootstrap_sample(txs):
    '''Bootstrap resampling of txs.'''
    n = len(txs)
    try:
        sample = [txs[int(random()*n)] for idx in range(n)]
    except IndexError:
        sample = [choice(txs) for idx in range(n)]
    sample.sort(key=lambda x: x[0], reverse=True)
    return sample


def _deps_check(entry, entries, remove_depped=False):
    if remove_depped:
        return not entry['depends']
    deps = [entries.get(dep_id) for dep_id in entry['depends']]
    return all([dep['inblock'] if dep else True for dep in deps])
