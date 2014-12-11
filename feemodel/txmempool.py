from time import time, sleep
from copy import deepcopy
import threading
import sqlite3
from bitcoin.core import COIN, b2lx
from config import config, historyFile
from feemodel.util import proxy, logWrite

pollPeriod = config['pollPeriod'] 

class TxMempoolThread(threading.Thread):
    def __init__(self,mempool):
        super(TxMempoolThread, self).__init__()
        self.mempool = mempool
        self._stop = threading.Event()

    def run(self):
        print("Starting mempool.")
        while not self._stop.is_set():
            self.mempool.update()
            self._stop.wait(timeout=pollPeriod)
        print("Ending mempool.")

    def stop(self):
        self._stop.set()


class TxMempool(object):
    # Have to handle RPC errors
    def __init__(self, model, writeHistory=True):
        self.model = model
        self.bestSeenBlock = proxy.getblockcount()
        self.mapTx = proxy.getrawmempool(verbose=True)
        self.writeHistory = writeHistory
    
    def update(self):
        currHeight = proxy.getblockcount()
        if currHeight > self.bestSeenBlock:
            self.processBlocks(currHeight)
        else:
            self.mapTx = proxy.getrawmempool(verbose=True)

    def processBlocks(self, currHeight):
        blockTime = time()
        blocks = []
        for blockHeight in range(self.bestSeenBlock+1, currHeight+1):
            blockData = proxy.getblock(proxy.getblockhash(blockHeight))
            blockSize = len(blockData.serialize())
            blockTxList = [b2lx(tx.GetHash()) for tx in blockData.vtx]
            entries = deepcopy(self.mapTx)
            numMempoolTxsInBlock = 0

            for txid, entry in entries.iteritems():
                if txid in blockTxList:
                    entry['inBlock'] = True
                    numMempoolTxsInBlock += 1
                    del self.mapTx[txid]
                else:
                    entry['inBlock'] = False
                entry['leadTime'] = blockTime - entry['time']
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']

            blocks.append(Block(entries,blockHeight,blockSize,blockTime))
            logWrite(str(numMempoolTxsInBlock) + ' of ' + 
                str(len(blockTxList)-1) + ' in block ' + str(blockHeight))

        self.bestSeenBlock = proxy.getblockcount()
        mapTxNew = proxy.getrawmempool(verbose=True)

        conflicts = set(self.mapTx) - set(mapTxNew)
        self.mapTx = mapTxNew
               
        for block in blocks:
            block.removeConflicts(conflicts)

        self.model.pushBlocks(blocks)

        if self.bestSeenBlock != currHeight:
            logWrite('We skipped a block here.')


class Block(object):
    def __init__(self, entries, blockHeight, blockSize, blockTime):
        self.entries = entries
        self.height = blockHeight
        self.size = blockSize
        self.time = blockTime

    def removeConflicts(self, conflicts):
        for txid in self.entries.keys():
            if txid in conflicts:
                del self.entries[txid]

