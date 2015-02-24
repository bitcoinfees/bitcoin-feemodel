'''Testing of TxMempool thread'''

import unittest
import os
import logging
from time import sleep

import feemodel.txmempool
from feemodel.txmempool import TxMempool, MemBlock
from testproxy import proxy

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s:%(levelname)s:%(message)s')
feemodel.txmempool.proxy = proxy
tmpdbfile = 'data/tmptest.db'
dbfile = 'data/test.db'

class TxMempoolTest(unittest.TestCase):
    def test_A(self):
        proxy.on = False
        proxy.blockcount = 333930
        b = MemBlock.read(333931, dbfile=dbfile)
        proxy.rawmempool = get_rawmempool(b)
        mempool = TxMempool(dbfile=tmpdbfile)
        with mempool.context_start():
            sleep(50)
            proxy.on = True
            sleep(20)
            proxy.blockcount = 333931
            sleep(10)

        b_read = MemBlock.read(333931, dbfile=tmpdbfile)
        self.assertNotEqual(b_read, b)
        b.time = b_read.time
        for entry in b.entries.values():
            entry.leadtime = None
        for entry in b_read.entries.values():
            entry.leadtime = None
        self.assertEqual(b_read, b)

    def tearDown(self):
        if os.path.exists(tmpdbfile):
            os.remove(tmpdbfile)


def get_rawmempool(memblock):
    '''Get rawmempool from a memblock.'''
    rawmempool = {
        txid: {
            'size': entry.size,
            'fee': entry.fee,
            'time': entry.time,
            'currentpriority': entry.currentpriority,
            'startingpriority': entry.startingpriority,
            'depends': entry.depends,
            'height': entry.height
        }
        for txid, entry in memblock.entries.items()
    }
    return rawmempool

if __name__ == '__main__':
    unittest.main()
