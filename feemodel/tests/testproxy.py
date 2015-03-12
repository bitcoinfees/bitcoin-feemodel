import os
from copy import deepcopy
from feemodel.util import BlockingProxy

history_file = os.path.join(os.path.dirname(__file__), 'data/test.db')

class TestProxy(BlockingProxy):
    '''A class that mimics bitcoin.rpc.Proxy for testing purposes.'''

    def __init__(self):
        self.on = True
        self.blockcount = 333954
        self.rawmempool = {}
        super(TestProxy, self).__init__()

    def getblockcount(self):
        if self.on:
            return self.blockcount
        else:
            raise Exception

    def poll_mempool(self):
        if self.on:
            return self.blockcount, deepcopy(self.rawmempool)
        else:
            raise Exception


class TestMempool(object):
    '''A class that mimics feemodel.TxMempool'''
    def __init__(self):
        from feemodel.txmempool import MemBlock
        self.b = MemBlock.read(333931, dbfile=history_file)
        for entry in self.b.entries.values():
            assert all([txid in self.b.entries for txid in entry.depends])

    def get_entries(self):
        return self.b.entries

proxy = TestProxy()
