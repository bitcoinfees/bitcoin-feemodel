from random import randint
from numpy.random import dirichlet
from scipy.special import comb, beta

timeDeltaMargin = 5
hardMaxBlockSize = 1e6 # The hard-coded block size limit
blockSizeStdDev = 0.05*hardMaxBlockSize # The model for actual max block sizes
defaultAlpha = 10 # value of alpha in the (alpha, 1) beta-binomial model
numPolicies = 5 
minFeeRateInitRange = (100, 50000)
maxBlockSizeInitRange = (0, hardMaxBlockSize)

class Policy:
    def __init__(self, minFeeRate, maxBlockSize, weight):
        self.minFeeRate = minFeeRate
        self.maxBlockSize = maxBlockSize
        self.weight = weight

class ModelParams:
    def __init__(self, numPolicies=numPolicies, alpha=defaultAlpha):
        policyWeights = dirichlet([1]*numPolicies)
        self.policies = [Policy(randint(*minFeeRateInitRange),
            randint(*maxBlockSizeInitRange),policyWeights[i]) for i in range(numPolicies)]
        self.alpha = alpha

class BlockStats:
    def __init__(self, blockData):
        '''blockData is the list contained in the *.pickle files'''
        self.blockData = blockData
        self.stats = filter(self._txCriteria, blockData)
        # Filter out by mintime
        try:
            mintime = min([tx['timedelta'] for tx in self.stats if tx['inBlock']])
        except ValueError:
            mintime = 0

        mintime += timeDeltaMargin

        self.stats = filter(lambda x: x['timedelta'] > mintime, self.stats)
        self.stats.sort(key=lambda x: x['feeRate'], reverse=True)

    def _txCriteria(self, tx):
        '''tx must have > 0 feeRate and all its mempool dependencies must be inBlock.'''
        deps = [depc for depc in self.blockData if depc['txid'] in tx['dependencies']]
        return tx['feeRate'] and all([dep['inBlock'] for dep in deps])

    def calcWeights(self, params):
        weights = [[0]*len(params.policies) for i in ('fee','size')]
        for pidx,policy in enumerate(params.policies):
            for lidx,limitState in enumerate(('fee', 'size')):
                likelihood = self.calcLikelihood(policy,limitState,params.alpha)

    def calcLikelihood(self, policy, limitState, alpha):
        if limitState == 'fee':
            n = len(self.stats)
            k = len([1 for tx in stats if tx['feeRate'] >= policy.minFeeRate and tx['inBlock']
                or tx['feeRate'] < policy.minFeeRate and not tx['inBlock']])
            return pbb(k,n,alpha)
        elif limitState == 'size':
            

def pbb(k,n,alpha,b=1):
    return comb(n,k)*beta(k+alpha,n-k+b)/beta(alpha,b)




