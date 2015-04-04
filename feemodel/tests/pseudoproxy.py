'''A pseudo proxy for testing purposes.'''

from bitcoin.core import CBlock
from bitcoin.rpc import JSONRPCException

import feemodel.util
from feemodel.util import load_obj
import feemodel.txmempool
from feemodel.txmempool import MemBlock
from feemodel.tests.config import blockdata, memblock_dbfile as dbfile


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

    def getrawmempool(self, verbose=True):
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

    def set_rawmempool(self, height):
        '''Set the rawmempool from test memblock with specified height.'''
        b = MemBlock.read(height, dbfile=dbfile)
        self.rawmempool = rawmempool_from_mementries(b.entries)


proxy = PseudoProxy()


def install():
    '''Substitutes the real proxy with our pseudo one.'''
    feemodel.util.proxy = proxy
    feemodel.txmempool.proxy = proxy


def rawmempool_from_mementries(entries):
    '''Convert mementries to rawmempool format.'''
    rawmempool = {}
    attrs = [
        'currentpriority',
        'startingpriority',
        'fee',
        'depends',
        'height',
        'size',
        'time'
    ]
    for txid, entry in entries.items():
        rawentry = {}
        for attr in attrs:
            rawentry[attr] = getattr(entry, attr)
        rawmempool[txid] = rawentry

    return rawmempool
