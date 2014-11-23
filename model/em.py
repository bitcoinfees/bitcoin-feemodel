from model.util import getFees, getBlockSize, getBlocks, proxy
from model.config import dbFile, config
from numpy import logspace, linspace, diff
from numpy.random import dirichlet, multinomial
from math import log
from random import randint
from operator import mul
import sqlite3

numPolicies = config['em']['numPolicies']
defaultAlpha = config['em']['defaultAlpha']
feeRateRange = config['em']['feeRateRange']
hardMaxBlockSize = config['em']['hardMaxBlockSize']
randomizeWeights = config['em']['randomizeWeights']
randomizeFee = config['em']['randomizeFee']
maxAlphaIterations = config['em']['maxAlphaIterations']
alphaRange = config['em']['alphaRange']
randomizeMaxBlockSizes = config['em']['randomizeMaxBlockSizes']
isStochastic = config['em']['isStochastic']

logTable = [None] + map(log, range(1, int(hardMaxBlockSize/100)))

class FeeTx:
    def __init__(self, feeTuple):
        self.feeRate = feeTuple[0]
        self.inBlock = bool(feeTuple[1])
        self.size = feeTuple[2]

    def __repr__(self):
        return "Tx(feerate: %d, inblock: %d, size: %d" % (
            self.feeRate, self.inBlock, self.size)

class BlockStats:
    def __init__(self, blockHeight, db):
        self.feeStats = [FeeTx(feeTuple) for feeTuple in getFees(blockHeight, db=db)]
        self.feeStats.sort(key=lambda x: x.feeRate, reverse=True)
        self.blockSize = getBlockSize(blockHeight, db=db)[0]
        self.blockHeight = blockHeight
        self.weights = None

    # def calcLikelihood(self, minFeeRate, maxBlockSize, alpha):
    #     n = k = 0
    #     modBlockSize = self.blockSize

    #     for tx in self.feeStats:
    #         if tx.feeRate >= minFeeRate:
    #             if tx.inBlock:
    #                 k += 1
    #                 n += 1
    #                 modBlockSize -= tx.size
    #             else:
    #                 if tx.size + modBlockSize
    #         else:

    def getDLLbyMinFee(self,maxBlockSize,alpha,weight):
        if self.blockSize > maxBlockSize:
            return (_calcpll(0,len(self.feeStats),alpha,weight), [])

        pll = []
        minFeeRates = []
        n = len(self.feeStats)
        k = len([1 for tx in self.feeStats if not tx.inBlock])
        # prevFeeRate = self.feeStats[0].feeRate+1
        prevFeeRate = float("inf")

        for tx in self.feeStats:
            currFeeRate = tx.feeRate
            if currFeeRate < prevFeeRate:
                # pll.append((prevFeeRate, _pll(k,n,alpha,weight)))
                pll.append(_calcpll(k,n,alpha,weight))
                minFeeRates.append(prevFeeRate)
                prevFeeRate = currFeeRate


            if tx.inBlock:
                k += 1
            elif tx.size + self.blockSize > maxBlockSize:
                k -= 1
                n -= 1
            else:
                k -= 1

        pll.append(_calcpll(k,n,alpha,weight))
        minFeeRates.append(currFeeRate)

        # pllDiff = diff(pll)
        dll = diff(pll)

        return (pll[0], zip(minFeeRates[1:], dll))

    def getDLLbyMaxBlockSize(self, minFeeRate, alpha, weight):
        pll = []
        maxBlockSizes = []
        nmax = n = len(self.feeStats)

        k = len([1 for tx in self.feeStats if 
            tx.feeRate >= minFeeRate and tx.inBlock or
            tx.feeRate < minFeeRate and not tx.inBlock])

        txBound = [tx.size + self.blockSize for tx in self.feeStats if 
            tx.feeRate >= minFeeRate and not tx.inBlock]

        txBound.sort(reverse=True)

        # if not txBound:
        #     raise ValueError("No txs in block " + str(self.blockHeight))

        prevMax = float("inf")

        for txMax in txBound:
            currMax = txMax
            if currMax < prevMax:
                pll.append(_calcpll(k,n,alpha,weight))
                maxBlockSizes.append(prevMax)
                prevMax = currMax

            n -= 1
        pll.append(_calcpll(k,n,alpha,weight))
        pll.append(_calcpll(0,nmax,alpha,weight))
        if 'currMax' in locals():
            maxBlockSizes.append(currMax)
        else:
            maxBlockSizes.append(prevMax)
        maxBlockSizes.append(self.blockSize-1)

        dll = diff(pll)

        return (pll[0], zip(maxBlockSizes[1:], dll))



    # def getKNbyMinFee(self,maxBlockSize):
    #     if self.blockSize > maxBlockSize:
    #         return None
    #     kn = []
    #     n = len(self.feeStats)
    #     k = len([1 for tx in self.feeStats if not tx.inBlock])
    #     prevFeeRate = self.feeStats[0].feeRate+1

    #     for tx in self.feeStats:
    #         currFeeRate = tx.feeRate
    #         if currFeeRate < prevFeeRate:
    #             kn.append((prevFeeRate, k, n))
    #             prevFeeRate = currFeeRate

    #         if tx.inBlock:
    #             k += 1
    #         elif tx.size + self.blockSize > maxBlockSize:
    #             n -= 1

    #     kn.append((currFeeRate,k,n))
    #     return kn

    def calcWeights(self, params):
        weights = [None]*len(params.policies)
        likelihoods = [None]*len(params.policies)
        for policy in params.policies:
            likelihoods[policy.idx] = self.calcLikelihood(policy, params.alpha)
            weights[policy.idx] = likelihoods[policy.idx]*policy.weight
        weightSum = sum(weights)
        if weightSum > 0:
            self.weights = map(lambda x: x/weightSum, weights)
        else:
            self.weights = [0]*len(params.policies)
            print ("Warning all weights are zero.")

        if isStochastic:
            weights = multinomial(1, self.weights)
            self.weights = map(float, weights)

        self.likelihoods = likelihoods

    def getKN(self,policy):
        k = n = 0

        for tx in self.feeStats:
            if tx.feeRate >= policy.minFeeRate:
                if tx.inBlock:
                    k += 1
                    n += 1
                elif not (tx.size + self.blockSize > policy.maxBlockSize):
                    n += 1
            else:
                if not tx.inBlock:
                    k += 1
                n += 1

        return k,n


    def calcLikelihood(self, policy, alpha):
        if self.blockSize > policy.maxBlockSize:
            return 0

        k,n = self.getKN(policy)

        return _pbb(k,n,alpha)

    def showTailFees(self,n):
        return self.feeStats[-n:]

    def weightsEntropy(self):
        return sum(map(lambda x: -x*log(x), self.weights))



class Policy:
    def __init__(self, idx, minFeeRate, maxBlockSize, weight):
        self.minFeeRate = minFeeRate
        self.maxBlockSize = maxBlockSize
        self.weight = weight
        self.idx = idx

    def maximize(self, blocks, alpha):
        self.maximizeMinFeeRate(blocks,alpha)
        self.maximizeMaxBlockSize(blocks,alpha)

    def maximizeMaxBlockSize(self, blocks, alpha):
        dlls = []
        maxL = 0

        for block in blocks:
            dllData = block.getDLLbyMaxBlockSize(self.minFeeRate, alpha, block.weights[self.idx])
            dlls += dllData[1]
            maxL += dllData[0]

        dlls.sort(key=lambda x: x[0], reverse=True)

        argMaxL = float("inf")
        currL = maxL
        prevMaxSize = float("inf")

        for dll in dlls:
            if dll[0] < prevMaxSize:
                if currL > maxL:
                    maxL = currL
                    argMaxL = prevMaxSize
                prevMaxSize = dll[0]

            currL += dll[1]

        if currL > maxL:
            argMaxL = prevMaxSize

        argMaxL = min(argMaxL, hardMaxBlockSize)

        self.maxBlockSize = argMaxL
        return dlls
      

    def maximizeMinFeeRate(self, blocks,alpha):
        dlls = []
        maxL = 0

        for block in blocks:
            dllData = block.getDLLbyMinFee(self.maxBlockSize, alpha, block.weights[self.idx])
            dlls += dllData[1]
            maxL += dllData[0]

        dlls.sort(key=lambda x: x[0], reverse=True)

        argMaxL = float("inf")        
        currL = maxL 
        prevFeeRate = float("inf")

        for dll in dlls:
            if dll[0] < prevFeeRate:
                if currL > maxL:
                    maxL = currL
                    argMaxL = prevFeeRate
                prevFeeRate = dll[0]

            currL += dll[1]

        if currL > maxL:
            argMaxL = prevFeeRate

        self.minFeeRate = argMaxL

    def __repr__(self):
        return "Policy(MinFeeRate: %f, MaxBlockSize: %f, Weight: %.3f)" % (
            self.minFeeRate, self.maxBlockSize, self.weight)
            

class Params:
    def __init__(self, policies=None, numPolicies=numPolicies, alpha=defaultAlpha):
        if policies:
            self.policies = policies
        else:
            if randomizeFee:
                minFeeRates = [randint(*feeRateRange) for n in range(numPolicies)]
            else:
                minFeeRates = logspace(log(feeRateRange[0],10), log(feeRateRange[1],10), numPolicies)

            if randomizeWeights:
                weights = dirichlet([1]*numPolicies)
            else:
                weights = [1./numPolicies for n in range(numPolicies)]

            if randomizeMaxBlockSizes:
                sizes = [randint(1000,hardMaxBlockSize) for n in range(numPolicies)]
            else:
                # sizes = [hardMaxBlockSize for n in range(numPolicies)]
                sizes = linspace(0.1*hardMaxBlockSize, hardMaxBlockSize, numPolicies)

            sizes = [hardMaxBlockSize for i in range(numPolicies)]

            self.policies = [Policy(idx, minFeeRate, sizes[idx], weights[idx]) 
                for idx,minFeeRate in enumerate(minFeeRates)]
        
        self.alpha = alpha

    def maximize(self, blocks):
        newWeights = [None]*len(self.policies)
        for policy in self.policies:
            newWeights[policy.idx] = sum([block.weights[policy.idx] for block in blocks])
            policy.maximize(blocks, self.alpha)
        # Don't forget to maximize alpha here

        # Set up the difference function of alpha, use the secant method to find the roots
        # alphaDiff = _getAlphaDiff(blocks, self.policies)
        # self.alpha = _findAlpha(alphaDiff, self.alpha)
        self.alpha = _getAlphaML(blocks, self.policies)

        newWeightsSum = sum(newWeights)
        if newWeightsSum:
            for policy in self.policies:
                policy.weight = newWeights[policy.idx]/newWeightsSum
        else:
            raise ValueError("Something went real bad.")

        return newWeights



class EM:
    def __init__(self, policies=None):
        self.params = Params(policies=policies)
        self.blocks = []

    def addBlock(self, blockHeight, db):
        self.blocks.append(BlockStats(blockHeight, db))

    def eStep(self):
        for block in self.blocks:
            block.calcWeights(self.params)

    def mStep(self):
        self.params.maximize(self.blocks)

def _pbb(k,n,alpha):
    r = (float(k+i)/(n+i) for i in xrange(1,alpha))

    return reduce(mul, r, 1)*alpha/(n+alpha)

def _pbb2(k,n,alpha):

    numerator = reduce(mul, range(k+1,k+alpha), 1)
    denominator = reduce(mul, range(n+1, n+alpha+1), 1)
    return float(numerator)*alpha/denominator

def _calcpll(k,n,alpha,weight):
    return weight*(sum(logTable[k+1:k+alpha]) - sum(logTable[n+1:n+alpha+1]))

# def _calcplln(n,alpha,weight):
#     return weight*(sum(logTable[n+1:n+alpha+1]))

def _getAlphaML(blocks, policies):
    kn = [(block.weights[policy.idx],) + block.getKN(policy) 
        for block in blocks for policy in policies]

    alphaList = range(*alphaRange)
    LL = [sum([_calcpll(k,n,alpha,weight)+weight*logTable[alpha] for weight,k,n in kn])
        for alpha in alphaList]

    maxLL = max(enumerate(LL), key=lambda x: x[1])
    return alphaList[maxLL[0]]


def _getAlphaDiff(blocks, policies):
    kn = [(block.weights[policy.idx],) + block.getKN(policy) 
            for block in blocks for policy in policies]

    alphaDiff = lambda a: sum([weight*(logTable[k+a]-logTable[n+a+1]
        +logTable[a+1]-logTable[a]) for weight,k,n in kn])

    return alphaDiff

def _nextAlpha(f, a1,a2):
    a1 = max(a1, 3)
    a2 = max(a2, 2)
    # if a1 < 1 or a2 < 1:
    #     raise ValueError("Bad alpha values.")
    return int(round(a1 - f(a1)*(a1-a2)/(f(a1)-f(a2))))

def _findAlpha(f, alphaInit):


    alphaPrev = alphaInit 
    alphaPrevPrev = alphaPrev - 1

    for i in xrange(maxAlphaIterations):
        alphaNext = _nextAlpha(f, alphaPrev, alphaPrevPrev)
        if alphaNext == alphaPrev:
            return alphaNext
        else:
            alphaPrevPrev = alphaPrev
            alphaPrev = alphaNext

    raise ValueError("Max alpha iterations reached.")


# if __name__ == '__main__':
def run(policies=None):
    db = sqlite3.connect(dbFile)
    maxblocks = 500
    currHeight = proxy.getblockcount()
    blocks = getBlocks(currHeight-maxblocks, db)
    try:
        emObj = EM(policies)
        for n,blockHeight in enumerate(blocks):            
            emObj.addBlock(blockHeight,db)

        return emObj
    finally:
        db.close()







