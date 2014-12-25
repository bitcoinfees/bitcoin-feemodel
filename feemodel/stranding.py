from random import random

def txPreprocess(block, removeHighPriority=False, removeDeps=False, allowZeroFee=False):
    try:
        minLeadTime = min([entry['leadTime'] for entry in block.entries.itervalues()
            if entry['inBlock']])
    except ValueError:
        minLeadTime = 0

    txs = [(entry['feeRate'], entry['inBlock']) for entry in block.entries.itervalues()
        if _depsCheck(entry, block.entries, removeDeps)
        and entry['leadTime'] >= minLeadTime
        and (entry['feeRate'] if not allowZeroFee else True)
        and (entry['currentpriority'] < 57.6e6 if removeHighPriority else True)]

    txs.sort(key=lambda x: x[0], reverse=True)
    return txs

def _depsCheck(entry, entries, removeDeps=False):
    if removeDeps:
        return not entry['depends']
    deps = [entries.get(depId) for depId in entry['depends']]
    return all([dep['inBlock'] if dep else True for dep in deps])

def calcStrandingFeeRate(txs, bootstrap=True):
    '''
    txs is [(feeRate, inBlock) for some list of txs]
    It's assumed that the list is sorted in descending order of feeRate 
    '''
    if not len(txs):
        raise ValueError('Empty txs list')

    sfr = calcStrandingSingle(txs)
    sidx = 0
    
    try:
        while txs[sidx][0] >= sfr:
            sidx += 1
    except IndexError:
        pass

    abovek = sum(txs[idx][1] for idx in xrange(sidx))
    belowk = sum(not txs[idx][1] for idx in xrange(sidx,len(txs)))

    aboven = sidx
    belown = len(txs) - sidx

    if bootstrap and sfr != float("inf"):
        try:
            altBiasRef = txs[sidx][0]
        except IndexError:
            altBiasRef = 0

        bootstrapEstimates = [calcStrandingSingle(bootstrapSample(txs)) for i in range(1000)]
        mean = float(sum(bootstrapEstimates)) / len(bootstrapEstimates)
        std = (sum([(b-mean)**2 for b in bootstrapEstimates]) / (len(bootstrapEstimates)-1))**0.5

        biasRef = max((sfr, abs(mean-sfr)), 
            (altBiasRef, abs(mean-altBiasRef)), key=lambda x: x[1])[0]
        bias = mean - biasRef
    else:
        bias = std = mean = float("inf")

    return {"sfr": sfr, "bias": bias, "mean": mean, "std": std,
        "abovekn": (abovek, aboven), "belowkn": (belowk, belown)}

def bootstrapSample(txs):
    n = len(txs)
    sample = [txs[int(random()*n)] for idx in xrange(n)]
    sample.sort(key=lambda x: x[0], reverse=True)
    return sample

def calcStrandingSingle(txs):
    '''
    txs is [(feeRate, inBlock) for some list of txs]
    It's assumed that the list is sorted in descending order of feeRate 
    '''
    if not len(txs):
        raise ValueError('Empty txs list')

    cumk = 0
    maxk = 0
    maxidx = 0
    txs.insert(0, (float("inf"), 1))
    
    for idx,tx in enumerate(txs):
        cumk += 1 if tx[1] else -1
        if cumk > maxk:
            maxk = cumk
            maxidx = idx

    sfr = txs[maxidx][0]
    txs.pop(0)
    return sfr