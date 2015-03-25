'''Test app.transient.'''
import unittest
import logging
from time import sleep
from pprint import pprint
from feemodel.tests.testproxy import TestMempool, TestPoolsOnline, TestTxOnline
from feemodel.util import load_obj
from feemodel.app.transient import TransientOnline

dbfile = 'data/test.db'
refpools = load_obj('data/pe_ref.pickle')
reftxsource = load_obj('data/tr_ref.pickle')
statsref = load_obj('data/transientstats_ref.pickle')

logging.basicConfig(level=logging.DEBUG)


class TransientSimTests(unittest.TestCase):
    def test_A(self):
        transientonline = TransientOnline(
            TestMempool(),
            TestPoolsOnline(refpools),
            TestTxOnline(reftxsource))
        with transientonline.context_start():
            while transientonline.stats is None:
                sleep(1)
            stats = transientonline.stats
            print("Expected wait:")
            stats.expectedwaits.print_fn()
            print("Median wait:")
            stats.waitpercentiles[9].print_fn()
            print("Predicts for 10000 feerate:")
            pprint(zip(stats.predict(10000), statsref.predict(10000)))
            print("Predicts for 2679 feerate:")
            pprint(zip(stats.predict(2679), statsref.predict(2679)))
            print("Predicts for 2680 feerate:")
            pprint(zip(stats.predict(2680), statsref.predict(2680)))
            print("Predicts for 50000 feerate:")
            pprint(zip(stats.predict(50000), statsref.predict(50000)))
            print("Predicts for 0 feerate:")
            pprint(zip(stats.predict(0), statsref.predict(0)))

            self.assertTrue(all([w is None for w in stats.predict(2679)]))
            self.assertTrue(all([w is not None for w in stats.predict(2680)]))
            self.assertEqual(stats.predict(44444), stats.predict(44445))
            self.assertEqual(stats.expectedwaits(44444),
                             stats.expectedwaits(44445))
            minwait = stats.expectedwaits.waits[-1]
            self.assertIsNotNone(stats.expectedwaits.inv(minwait))
            self.assertIsNone(stats.expectedwaits.inv(minwait-1))
            self.assertEqual(10000, stats.numiters)

    def test_B(self):
        pass


if __name__ == '__main__':
    unittest.main()
