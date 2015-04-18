import unittest
from feemodel.txmempool import MemBlock
from feemodel.stranding import (tx_preprocess, calc_stranding_feerate,
                                _calc_min_leadtime)
from feemodel.tests.config import test_memblock_dbfile as dbfile

txs_refA = [
    (11000, True),
    (10000, True),
    (10000, False),
    (999, False),
    (999, False),
]
txs_refA *= 100
txs_refB = [
    (11000, False),
    (10000, False),
    (10000, False),
    (999, False),
    (999, False),
]
txs_refB *= 100
txs_refC = [
    (11000, True),
    (10000, True),
    (10000, True),
    (999, True),
    (999, True),
]
txs_refC *= 100
txs_refD = [
    (11000, False),
    (10000, False),
    (10000, False),
    (999, True),
    (999, True),
    (998, True),
]
txs_refE = txs_refD + [(998, True)]
txs_refD *= 100
txs_refE *= 100
txs_refF = [
    (0, False),
    (0, False),
    (0, True),
    (0, True),
    (0, True),
]
txs_refF *= 100

txs_refG = [
    (11000, True),
    (11000, False)
]
txs_refH = txs_refG + [(10000, True)]


class PreprocessTests(unittest.TestCase):

    def test_A(self):
        for height in range(333931, 333954):
            b = MemBlock.read(height, dbfile=dbfile)
            if b is not None:
                min_leadtime = _calc_min_leadtime(b)
                print("Block {}: the min leadtime is {}.".
                      format(height, min_leadtime))
                txs = tx_preprocess(b)
                for entry in b.entries.values():
                    if (entry.feerate, entry.inblock) not in txs:
                        self.assertTrue(
                            entry.is_high_priority() or
                            entry.leadtime < min_leadtime or
                            _depcheck(entry, b.entries) or
                            entry.isconflict
                        )


class SFRTests(unittest.TestCase):

    def test_A(self):
        '''Basic tests.'''
        self.check_sfr(txs_refA, 11000)
        # Assert that calc_stranding_feerate doesn't assume input is sorted
        txs_copy = list(reversed(txs_refA))
        self.check_sfr(txs_copy, 11000)
        self.check_sfr(txs_refB, float("inf"))
        self.check_sfr(txs_refC, 999)
        self.check_sfr(txs_refD, float("inf"))
        self.check_sfr(txs_refE, 998)
        self.check_sfr(txs_refF, 0)
        self.check_sfr(txs_refG, float("inf"))
        self.check_sfr(txs_refH, 10000)

    def check_sfr(self, txs, target):
        stats = calc_stranding_feerate(txs, bootstrap=True)
        sfr = stats['sfr']
        self.assertEqual(sfr, target)
        print(stats)


def _depcheck(entry, entries):
    deps = [entries.get(depid) for depid in entry.depends]
    if any([dep is None for dep in deps]):
        print("Warning couldn't find dep.")
    return any([not dep.inblock for dep in deps if dep is not None])


if __name__ == '__main__':
    unittest.main()
