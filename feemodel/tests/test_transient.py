'''Test app.transient.'''
import unittest
import logging
from time import sleep, time
from bisect import bisect
from math import log
from random import choice, seed

from feemodel.txmempool import MempoolState
from feemodel.tests.pseudoproxy import install, proxy

from feemodel.app.transient import TransientOnline
from feemodel.app.predict import WAIT_PERCENTILE_PTS, WAIT_MEDIAN_IDX
from feemodel.tests.config import poolsref, txref, transientref as statsref

logging.basicConfig(level=logging.DEBUG)

install()
seed(0)


class TransientSimTests(unittest.TestCase):
    def test_A(self):
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref))
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(1)
            stats = transientonline.stats
            print("Expected wait:")
            stats.expectedwaits.print_fn()
            print("Expected waits (ref):")
            statsref.expectedwaits.print_fn()
            print("Median wait (idx {}):".format(WAIT_MEDIAN_IDX))
            stats.waitpercentiles[WAIT_MEDIAN_IDX].print_fn()
            print("Median wait (ref) (idx {}):".format(WAIT_MEDIAN_IDX))
            statsref.waitpercentiles[WAIT_MEDIAN_IDX].print_fn()

            print("Comparing expected waits with ref:")
            for wait, waitref in zip(stats.expectedwaits.waits,
                                     statsref.expectedwaits.waits):
                logdiff = abs(log(wait) - log(waitref))
                print("wait/waitref is {}.".format(wait/waitref))
                self.assertLess(logdiff, 0.1)
            wait_idx = choice(range(len(WAIT_PERCENTILE_PTS)))
            print("Comparing {} percentile waits with ref:".
                  format(WAIT_PERCENTILE_PTS[wait_idx]))
            for wait, waitref in zip(
                    stats.waitpercentiles[wait_idx].waits,
                    statsref.waitpercentiles[wait_idx].waits):
                logdiff = abs(log(wait) - log(waitref))
                print("wait/waitref is {}.".format(wait/waitref))
                self.assertLess(logdiff, 0.1)

            self.assertEqual(stats.expectedwaits(44444),
                             stats.expectedwaits(44445))
            minwait = stats.expectedwaits.waits[-1]
            self.assertIsNotNone(stats.expectedwaits.inv(minwait))
            self.assertIsNone(stats.expectedwaits.inv(minwait-1))
            self.assertEqual(10000, stats.numiters)

            # Waits predict for various feerates and percentiles
            currtime = time()
            for feerate in [2680, 10000, 44444, 44445]:
                txpredict = stats.predict(feerate, currtime)
                self.assertEqual(txpredict.calc_pval(currtime+0), 1)
                self.assertEqual(txpredict.calc_pval(currtime+float("inf")), 0)
                for pctl in [0.05, 0.5, 0.9]:
                    wait_idx = bisect(WAIT_PERCENTILE_PTS, pctl) - 1
                    wait = stats.waitpercentiles[wait_idx](feerate)
                    print("{} wait for feerate of {} is {}.".
                          format(pctl, feerate, wait))
                    blocktime = currtime + wait
                    pval = txpredict.calc_pval(blocktime)
                    self.assertAlmostEqual(pval, 1-pctl)

            txpredict = stats.predict(2679, currtime)
            self.assertIsNone(txpredict)

    def test_B(self):
        '''Test iter constraints.'''
        # Test maxtime (equiv. update_time) and update loop.
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref),
            update_period=1,
            miniters=0,
            maxiters=10000)
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertIsNotNone(stats)
            self.assertLess(stats.timespent, 1.1)
            transientonline.stats = None
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            self.assertIsNotNone(stats)

        # Test miniters
        transientonline = TransientOnline(
            PseudoMempool(),
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref),
            update_period=0,
            miniters=1000,
            maxiters=10000)
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(1)
            stats = transientonline.stats
            self.assertEqual(stats.numiters, 1000)

    def test_C(self):
        '''Test empty mempool.'''
        mempool = PseudoMempool()
        mempool.state = MempoolState(333930, {})
        transientonline = TransientOnline(
            mempool,
            PseudoPoolsOnline(poolsref),
            PseudoTxOnline(txref),
            update_period=3,
            miniters=0,
            maxiters=10000)
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(0.1)
            stats = transientonline.stats
            print("Expected wait:")
            stats.expectedwaits.print_fn()


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
