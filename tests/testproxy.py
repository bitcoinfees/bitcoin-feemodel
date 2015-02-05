from feemodel.util import BlockingProxy
from feemodel.txmempool import MemBlock

class TestProxy(BlockingProxy):
    '''A class that mimics bitcoin.rpc.Proxy for testing purposes.'''

    def getblockcount(self):
        return 333954

class TestMempool(object):
    '''A class that mimics feemodel.TxMempool'''
    def __init__(self):
        self.b = MemBlock.read(333931, dbfile='data/test.db')
        for entry in self.b.entries.values():
            assert all([txid in self.b.entries for txid in entry.depends])

    def get_entries(self):
        return self.b.entries

proxy = TestProxy()
