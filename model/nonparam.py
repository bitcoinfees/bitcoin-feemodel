from model.config import dbReadFile, config
from generic import FeeTx, PriorityTx
from util import getFees,getBlockSize,getBlocks,getBlockData
from collections import defaultdict
from math import log, sqrt
from scipy.stats import norm
import sqlite3

dbFile = dbReadFile
alphaRange = config['np']['alphaRange']
OIRange = config['np']['OIRange']
hardMaxBlockSize = config['generic']['hardMaxBlockSize']
logTable = [None] + map(log, range(1, int(hardMaxBlockSize/100)))

class BlockStats:
    def __init__(self,blockHeight,blockSize,feeStats,priorityStats):
        self.feeStats = feeStats
        self.feeStats.sort(key=lambda x: x.feeRate, reverse=True)
        self.priorityStats = priorityStats
        self.priorityStats.sort(key=lambda x: x.priority, reverse=True)
        self.priorityBlockSize = sum([tx.size for tx in self.priorityStats if tx.inBlock])
        self.blockSize = blockSize
        self.blockHeight = blockHeight
        self.calcML()

    def calcML(self):
        n = len(self.feeStats)
        k = len([1 for tx in self.feeStats if not tx.inBlock])

        dkvals = defaultdict(int)

        for tx in self.feeStats:
            dkvals[tx.feeRate] += 1 if tx.inBlock else -1

        cumk = 0
        cumkmax = 0
        argkmax = float("inf")

        self.kvals = []
        
        dkvals = sorted(dkvals.items(), key=lambda x: x, reverse=True)
        for feeRate, kdelta in dkvals:
            cumk += kdelta
            self.kvals.append((feeRate,cumk+k))
            if cumk >= cumkmax:
                argkmax = feeRate
                cumkmax = cumk

        self.minFeeRate = argkmax
        self.k = k + cumkmax
        self.n = n

    # Gotta redo this to make less susceptible to noise
    def calcOI(self,alpha):
        # http://mathformeremortals.wordpress.com/2013/01/12/a-numerical-second-derivative-from-three-points/
        if self.minFeeRate != float('inf'):
            midx = [idx for idx,kval in enumerate(self.kvals) if kval[0] == self.minFeeRate]
            if not midx:
                raise ValueError("This shouldn't happen.")
            midx = midx[0]

            x = [None]*3
            y = [None]*3

            x[1],y[1] = self.kvals[midx] 
            try:
                i0 = midx+1
                while self.kvals[i0][1] > y[1] - OIRange:
                    i0 += 1
                x[0],y[0] = self.kvals[i0]
            except IndexError:
                x[0] = 0
                y[0] = self.kvals[-1][1]

            try:
                i2 = midx-1
                while self.kvals[i2][1] > y[1] - OIRange:
                    i2 -= 1
                x[2],y[2] = self.kvals[i2]
            except IndexError:
                x[2] = x[1]+x[1]-x[0]
                y[2] = self.kvals[0][1]

            y = map(lambda k: _calcpll(k, self.n, alpha), y)
            f = _dFn(x)

            self.OI = -sum([f[i](y[i]) for i in range(3)])
            self.std = sqrt(1./self.OI)

        else:
            self.OI = None
            self.std = float("inf")

    def feecdf(self, x):
        if self.minFeeRate != float("inf"):
            return norm.cdf(x, self.minFeeRate, self.std)
        else:
            return 0

    def feepdf(self, x):
        if self.minFeeRate != float("inf"):
            return norm.pdf(x, self.minFeeRate, self.std)
        else:
            return 0

    def __repr__(self):
        return "BlockStats(minFeeRate: %.0f, k: %d, n: %d, blockSize: %d, blockHeight: %d, std: %.1f)" % (
            self.minFeeRate,self.k,self.n,self.blockSize,self.blockHeight,self.std)

class NP:
    def __init__(self, blockHeightRange):
        self.blocks = []
        db = sqlite3.connect(dbFile)
        # blockHeights = getBlocks(*blockHeightRange, db=db)
        try:
            fees, priority, blockSizes = getBlockData(*blockHeightRange, db=db)
            for blockHeight, blockSize in blockSizes:
                blockFees = [FeeTx(f) for f in fees if f[3] == blockHeight]
                blockPriority = [PriorityTx(p) for p in priority if p[3] == blockHeight]
                self.blocks.append(BlockStats(blockHeight,blockSize,blockFees,blockPriority))
        finally:
            db.close()
        self.numBlocks = len(self.blocks)
        self.minFeeRates = [block.minFeeRate for block in self.blocks]
        self.calcAlpha()

        for block in self.blocks:
            block.calcOI(self.alpha)

    def inclusionProb(self,feeRates,gaussianKernel=True):
        if gaussianKernel:
            return [self.feecdf(feeRate) for feeRate in feeRates]
        else:
            return [len([1 for feepoint in self.minFeeRates if feepoint <= feeRate])/float(self.numBlocks) 
                for feeRate in feeRates]

    def calcAlpha(self):
        alphaGrid = range(*alphaRange)

        LL = [sum([_calcpll(block.k, block.n, alpha) for block in self.blocks]) 
            for alpha in alphaGrid]

        maxLL = max(enumerate(LL), key=lambda x: x[1])
        self.alpha = alphaGrid[maxLL[0]]

    def feecdf(self, feeRate):
        return sum([block.feecdf(feeRate) for block in self.blocks])/len(self.blocks)

    def feepdf(self, feeRate):
        return sum([block.feepdf(feeRate) for block in self.blocks])/len(self.blocks)

def _calcpll(k,n,alpha):
    return sum(logTable[k+1:k+alpha]) - sum(logTable[n+1:n+alpha+1]) + logTable[alpha]

def _dFn(x):
    fn1 = lambda y: y*2./(x[1] - x[0])/(x[2] - x[0])
    fn2 = lambda y: y*-2./(x[2] - x[1])/(x[1] - x[0])
    fn3 = lambda y: y*2./(x[2] - x[1])/(x[2] - x[0])
    return [fn1, fn2, fn3]

def run(blockHeightRange):
    return NP(blockHeightRange)









