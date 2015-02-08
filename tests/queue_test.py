import unittest
from feemodel.queuestats import QueueStats


class QueuestatsTest(unittest.TestCase):
    def setUp(self):
        self.feerate = 1000
        self.qstats = QueueStats([self.feerate])

    def test_nonestranded(self):
        interval = 100
        for i in range(10):
            self.qstats.next_block(i, interval, self.feerate)
        self.assertEqual(self.qstats.stats[0].avgwait, interval/2.)
        self.assertFalse(self.qstats.stats[0].strandedblocks)

    def test_initstranded(self):
        interval = 100
        for i in range(2):
            self.qstats.next_block(i, interval, self.feerate+1)
        i += 1
        self.qstats.next_block(i, interval, self.feerate-1)
        avgwait = 4.5*interval/3
        self.assertEqual(self.qstats.stats[0].totaltime, 3*interval)
        self.assertEqual(self.qstats.stats[0].avgwait, avgwait)
        self.assertFalse(self.qstats.stats[0].strandedblocks)

    def test_allstranded(self):
        interval = 100
        for i in range(10):
            self.qstats.next_block(i, interval, self.feerate+1)

        self.assertFalse(self.qstats.stats[0])


if __name__ == '__main__':
    unittest.main()
