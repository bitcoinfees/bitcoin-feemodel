'''Test app.transient.'''
import unittest
import logging
from time import sleep, time
from bisect import bisect
from math import log
from pprint import pprint
from copy import deepcopy

from feemodel.simul.transient import transientsim
from feemodel.simul.simul import Simul
from feemodel.txmempool import MempoolState
from feemodel.util import DataSample
from feemodel.app.transient import TransientOnline
from feemodel.app.predict import WAIT_PERCENTILE_PTS
from feemodel.tests.config import (poolsref, txref,
                                   transientwaitsref as waitsref)
from feemodel.tests.pseudoproxy import install, proxy
from feemodel.tests.test_simul import init_entries

install()


class TransientRefCmp(unittest.TestCase):
    """Compare the wait times with the reference test data."""

    def test_A(self):
        sim = Simul(poolsref, txref)
        starttime = time()
        feepoints, waittimes = transientsim(
            sim,
            feepoints=waitsref[0],
            init_entries=init_entries,
            maxtime=600,
            maxiters=10000,
        )
        numiters = len(waittimes[0])
        timespent = time() - starttime

        print("Complete in {}s with {} iters.".format(timespent, numiters))
        avgwaittimes = map(lambda waits: sum(waits)/len(waits), waittimes)
        print("Sim:")
        pprint(zip(feepoints, avgwaittimes))
        print(sim.cap.capfn.approx())
        print(sim.cap.txbyteratefn.approx())
        print("Stablefeerate is {}".format(sim.stablefeerate))
        print("Ref:")
        for feerate, avgwait in zip(*waitsref):
            print(feerate, avgwait)

        for avgwait, avgwaitref in zip(avgwaittimes, waitsref[1]):
            logdiff = abs(log(avgwait) - log(avgwaitref))
            print("logdiff is {}.".format(logdiff))
            # Probabilistic test
            self.assertLess(logdiff, 0.1)


class TransientSamplingDist(unittest.TestCase):
    """Test the sampling distribution of the transient waittimes.

    We want to see if the sampling distribution of the mean waittime is as
    predicted.
    """
    def setUp(self):
        self.logger = logging.getLogger("feemodel")
        self.logger.setLevel(logging.INFO)

    def test_mean(self):
        sim = Simul(poolsref, txref)
        feepoints = [10000]

        means = []
        stds = []
        for i in range(100):
            feerates, waittimes = transientsim(
                sim,
                feepoints=feepoints,
                maxiters=400,
                miniters=400
            )
            w = waittimes[0]
            waitdata = DataSample(w)
            waitdata.calc_stats()
            means.append(waitdata.mean)
            stds.append(waitdata.std / len(waitdata)**0.5)
            if not i % 10:
                print("Finished iteration {}".format(i))

        estd = sum(stds) / len(stds)
        meandata = DataSample(means)
        meandata.calc_stats()
        print("Expected/actual std: {}/{}".format(estd, meandata.std))
        # Probabilistic test
        self.assertLess(abs(log(estd)-log(meandata.std)), 0.2)

    def tearDown(self):
        self.logger.setLevel(logging.DEBUG)


class TransientOnlineTests(unittest.TestCase):

    def test_A(self):
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref),
            update_period=3,
            miniters=0,
            maxiters=float("inf"))
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertIsNotNone(stats)
            print("First stats:")
            print("===========")
            print("Expected wait:")
            print(stats.expectedwaits)
            # self.assertEqual(stats.expectedwaits(46599),
            #                  stats.expectedwaits(46600))
            self.assertEqual(stats.expectedwaits(46609),
                             stats.expectedwaits(46610))
            minwait = stats.expectedwaits.waits[-1]
            self.assertIsNotNone(stats.expectedwaits.inv(minwait))
            self.assertIsNone(stats.expectedwaits.inv(minwait-1))

            currtime = 0
            for feerate in [1039, 10000, 46609, 46610]:
                txpredict = stats.predict(feerate, currtime)
                self.assertEqual(txpredict.calc_pval(currtime+0), 1)
                self.assertEqual(
                    txpredict.calc_pval(currtime+float("inf")), 0)
                for pctl in [0.05, 0.5, 0.9]:
                    wait_idx = bisect(WAIT_PERCENTILE_PTS, pctl) - 1
                    wait = stats.waitmatrix[wait_idx](feerate)
                    print("{} wait for feerate of {} is {}.".
                          format(pctl, feerate, wait))
                    blocktime = currtime + wait
                    pval = txpredict.calc_pval(blocktime)
                    self.assertAlmostEqual(pval, 1-pctl)

            txpredict = stats.predict(1038, currtime)
            self.assertIsNone(txpredict)

            for i in range(2):
                while transientonline.stats.timestamp == stats.timestamp:
                    sleep(0.1)
                stats = transientonline.stats
                print("#{} stats:".format(i+1))
                print("=============")
                print(stats.expectedwaits)

    def test_B(self):
        # Crazy high tx rate.
        txref_high = deepcopy(txref)
        txref_high.txrate = 1e6
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref_high),
            update_period=3,
            miniters=0,
            maxiters=float("inf"))
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertIsNotNone(stats)
            print("Crazy high txrate:")
            print("===========")
            print("Expected wait:")
            print(stats.expectedwaits)

        # Moderately high tx rate.
        txref_high = deepcopy(txref)
        txref_high.txrate = 100
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref_high),
            update_period=3,
            miniters=0,
            maxiters=float("inf"))
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertIsNotNone(stats)
            print("Moderately high txrate:")
            print("===========")
            print("Expected wait:")
            print(stats.expectedwaits)

    def test_C(self):
        # Test iter limits.
        MAXITERS = 5000
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref),
            update_period=100000,
            miniters=0,
            maxiters=MAXITERS)
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertLess(stats.numiters, MAXITERS*1.1)

        MINITERS = 5000
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref),
            update_period=0,
            miniters=MINITERS,
            maxiters=float("inf"))
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertLess(stats.numiters, MINITERS*1.1)


class PseudoMempool(object):
    '''A pseudo TxMempool'''

    def __init__(self):
        proxy.set_rawmempool(333931)
        proxy.blockcount = 333930
        self.state = MempoolState(*proxy.poll_mempool())


class PseudoPoolsOnline(object):

    def __init__(self, poolsestimate):
        self.poolsestimate = poolsestimate

    def get_pools(self):
        return self.poolsestimate

    def __nonzero__(self):
        return bool(self.poolsestimate)


class PseudoTxOnline(object):

    def __init__(self, txrate_estimator):
        self.txrate_estimator = txrate_estimator

    def get_txsource(self):
        return self.txrate_estimator

    def __nonzero__(self):
        return bool(self.txrate_estimator)


if __name__ == '__main__':
    unittest.main()
