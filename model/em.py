from random import randint
from numpy.random import dirichlet
from scipy.special import comb, beta
from config import config
from os import path
import cPickle as pickle
from operator import mul

timeDeltaMargin = 5
hardMaxBlockSize = 1e6 # The hard-coded block size limit
blockSizeStdDev = 0.05*hardMaxBlockSize # The model for actual max block sizes
defaultAlpha = 10 # value of alpha in the (alpha, 1) beta-binomial model
numPolicies = 5 
minFeeRateInitRange = (100, 50000)
maxBlockSizeInitRange = (0, hardMaxBlockSize)

datadir = config['em']['datadir']
dataformat = config['em']['dataformat']

class Policy:
    def __init__(self, minFeeRate, maxBlockSize, weight):
        self.minFeeRate = minFeeRate
        self.maxBlockSize = maxBlockSize
        self.weight = weight

    def maximize(self, weights, stats):
        pass

    def __repr__(self):
        return "Policy(MinFeeRate: %d, MaxBlockSize: %d, Weight: %.3f)" % (self.minFeeRate, self.maxBlockSize, self.weight)
        # return str(self.minFeeRate)+', ' + str(self.maxBlockSize) + ', ' + str(self.weight)


class ModelParams:
    def __init__(self, numPolicies=numPolicies, alpha=defaultAlpha):
        policyWeights = dirichlet([1]*numPolicies)
        self.policies = [Policy(randint(*minFeeRateInitRange),
            hardMaxBlockSize,policyWeights[i]) for i in range(numPolicies)]
        self.alpha = alpha

    # def __repr__(self):
    #     return self.policies, self.alpha

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
        self.blockSize = sum([tx['size'] for tx in self.stats if tx['inBlock']])

    def _txCriteria(self, tx):
        '''tx must have > 0 feeRate and all its mempool dependencies must be inBlock.'''
        deps = [depc for depc in self.blockData if depc['txid'] in tx['dependencies']]
        return tx['feeRate'] and all([dep['inBlock'] for dep in deps])

    def calcWeights(self, params):
        weights = [0]*len(params.policies)
        for pidx,policy in enumerate(params.policies):
            likelihood = self.calcLikelihood(policy,params.alpha)
            weights[pidx] = likelihood*policy.weight
        weightSum = sum(weights)
        if weightSum > 0:
            self.weights = map(lambda x: x/weightSum, weights)
        else:
            self.weights = [0]*len(params.policies)

    def calcLikelihood(self, policy, alpha):
        
        if self.blockSize > policy.maxBlockSize+blockSizeStdDev:
            return 0
        else:
            k = len([1 for tx in self.stats if tx['feeRate'] >= policy.minFeeRate and tx['inBlock']
                or tx['feeRate'] < policy.minFeeRate and not tx['inBlock']])
            n = len(self.stats)
            if self.blockSize > policy.maxBlockSize-blockSizeStdDev:
                try:
                    feeThreshold = min([tx['feeRate'] for tx in self.stats 
                        if tx['feeRate'] > policy.minFeeRate and tx['inBlock']])
                    
                    nDiscount = len([1 for tx in self.stats if tx['feeRate'] < feeThreshold
                        and tx['feeRate'] > policy.minFeeRate])
                except ValueError:
                    nDiscount = len([1 for tx in self.stats if tx['feeRate'] > policy.minFeeRate])

                n -= nDiscount

            return pbb(k,n,alpha)

class EM:
    def __init__(self):
        self.params = ModelParams()
        self.data = []
    
    def addBlock(self, blockData):
        self.data.append(BlockStats(blockData))

    def eStep(self):
        for block in self.data: 
            block.calcWeights(self.params)

    def mStep(self):
        newWeights = [0].len(self.params.policies)
        for pidx,policy in enumerate(self.params):
            weights = [block.weights[pidx] for block in self.data]
            policy.maximize(weights, self.data)
            newWeights[pidx] = sum(weights)
        newWeightsSum = sum(newWeights)
        for pidx,policy in enumerate(self.params):
            policy.weight = newWeights[pidx]/newWeightsSum
        # Remember to re-estimate alpha!

def pbb(k,n,alpha):
    numerator = reduce(mul, range(k+1,k+alpha), 1)
    denominator = reduce(mul, range(n+1, n+alpha+1), 1)
    return float(numerator)*alpha/denominator


def pbb2(k,n,alpha,b=1):
    return comb(n,k)*beta(k+alpha,n-k+b)/beta(alpha,b)

if __name__ == '__main__':

    blockRange = (329400, 329450)
    em = EM()

    for height in range(*blockRange):
        filename = path.join(datadir, str(height) + dataformat)
        try:
            with open(filename, 'rb') as f:
                b = pickle.load(f)
                # print "Loading block " + str(height) + '...'
                em.addBlock(b)
        except IOError:
            print "Block not found, moving on"









