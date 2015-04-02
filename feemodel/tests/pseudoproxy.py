'''A pseudo proxy for testing purposes.'''

from bitcoin.core import CBlock
from bitcoin.rpc import JSONRPCException

from feemodel.util import load_obj
from feemodel.tests.config import blockdata


class PseudoProxy(object):
    '''A pseudo proxy.

    getblock and getblockhash is available for blocks 333931-333953
    (the range of values in the memblock test db).

    set blockcount and rawmempool to the values you want to be returned
    by getblockcount or getrawmempool respectively (or equivalently,
    by poll_mempool).

    set on = False to simulate a connection error - raises JSONRPCException
    on all method calls.
    '''

    def __init__(self):
        self.blockcount = None
        self.rawmempool = None
        self.on = True
        self._blockhashes, blocks_ser = load_obj(blockdata)
        self._blocks = {}
        for blockhash, block_ser in blocks_ser.items():
            self._blocks[blockhash] = CBlock.deserialize(block_ser)

    def getblockhash(self, blockheight):
        if not self.on:
            raise JSONRPCException
        return self._blockhashes[blockheight]

    def getblock(self, blockhash):
        if not self.on:
            raise JSONRPCException
        return self._blocks[blockhash]

    def getrawmempool(self):
        if not self.on:
            raise JSONRPCException
        return self.rawmempool

    def getblockcount(self):
        if not self.on:
            raise JSONRPCException
        return self.blockcount

    def poll_mempool(self):
        if not self.on:
            raise JSONRPCException
        return self.blockcount, self.rawmempool


proxy = PseudoProxy()


def install():
    '''Substitutes the real proxy with our pseudo one.'''
    import feemodel.util
    feemodel.util.proxy = proxy
