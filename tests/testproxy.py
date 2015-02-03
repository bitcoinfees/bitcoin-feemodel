from bitcoin.rpc import Proxy

class TestProxy(Proxy):
    '''A class that mimics bitcoin.rpc.Proxy for testing purposes.'''

    def getblockcount(self):
        return 333954

proxy = TestProxy()
