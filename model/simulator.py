from numpy.random import poisson, gamma

defaultTxSize = 250
defaultFeeRate = 10000
feeResponse = 0.01 # Fees paid will increment or decrement by this proportion per block,
                   # depending on whether there was an increase in mempool backlog


class Demand:
    '''Demand for transactions, modeled as poisson process.'''
    def __init__(self, tps_ref, slope):
        '''tps_ref is transactions per second at fee rate of $defaultFeeRate, slope 
        is delta(tps) / delta(unit fee rate), constant.'''
        # TPS = a + b*feeRate
        self.b = slope
        self.a = tps_ref - slope*defaultFeeRate
        self.txSize = defaultTxSize
        self.currentFeeRate = defaultFeeRate

    def getTransactions(self, feeRate, period):
        '''Returns num transactions within a certain period (seconds), for a given fee rate,
        assuming poisson process'''
        tps = self.a + self.b*feeRate
        return poisson(tps*period)

    def getActualFeeRates(self, n):
        '''Return an array of gamma distributed random variables.
        Actual fees paid are assumed to have the distributed:
        (constant target feeRate) + Gamma(k, theta)'''
        return currentFeeRate + gamma(2, currentFeeRate/2., n)




