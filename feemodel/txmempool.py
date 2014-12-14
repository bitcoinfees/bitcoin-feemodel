from time import time, sleep
from copy import deepcopy
import threading
import sqlite3
import json
import os
import decimal
from bitcoin.core import COIN, b2lx
import feemodel.config
from feemodel.config import config, historyFile
from feemodel.util import proxy, logWrite

pollPeriod = config['pollPeriod']
keepHistory = config['keepHistory']

class TxMempoolThread(threading.Thread):
    def __init__(self,mempool):
        super(TxMempoolThread, self).__init__()
        self.mempool = mempool
        self._stop = threading.Event()

    def run(self):
        logWrite("Starting mempool.")
        while not self._stop.is_set():
            pthread = self.mempool.update()
            self._stop.wait(timeout=pollPeriod)
        if pthread:
            logWrite("Waiting for processBlocks to terminate...")
            pthread.join() # This is wrong. There might be > 1 thread.
        logWrite("Ending mempool.")

    def stop(self):
        self._stop.set()


class TxMempool(object):
    # Have to handle RPC errors
    def __init__(self, model):
        # Writehistory means write to db the mempool state at each block.
        # We keep <keepHistory> number of past blocks.
        self.model = model
        self.bestSeenBlock = proxy.getblockcount()
        self.mapTx = proxy.getrawmempool(verbose=True)
    
    def update(self):
        currHeight = proxy.getblockcount()
        if currHeight > self.bestSeenBlock:
            mapTxNew = proxy.getrawmempool(verbose=True)
            pthread = threading.Thread(target=TxMempool.processBlocks,
                args=(self.model, range(self.bestSeenBlock+1,currHeight+1),
                    deepcopy(self.mapTx), deepcopy(mapTxNew)))
            self.mapTx = mapTxNew
            self.bestSeenBlock = currHeight
            pthread.start()
            return pthread
        else:
            self.mapTx = proxy.getrawmempool(verbose=True)
            return None

    @staticmethod
    def processBlocks(model, blockHeightRange, currPool, newPool, blockTime=None):
        if not blockTime:
            blockTime = time()
        blocks = []
        for blockHeight in blockHeightRange:
            blockData = proxy.getblock(proxy.getblockhash(blockHeight))
            blockSize = len(blockData.serialize())
            blockTxList = [b2lx(tx.GetHash()) for tx in blockData.vtx]
            entries = deepcopy(currPool)
            numMempoolTxsInBlock = 0

            for txid, entry in entries.iteritems():
                if txid in blockTxList:
                    entry['inBlock'] = True
                    numMempoolTxsInBlock += 1
                    del currPool[txid]
                else:
                    entry['inBlock'] = False
                entry['leadTime'] = blockTime - entry['time']
                entry['feeRate'] = int(entry['fee']*COIN) * 1000 // entry['size']

            blocks.append(Block(entries,blockHeight,blockSize,blockTime))
            logWrite(str(numMempoolTxsInBlock) + ' of ' + 
                str(len(blockTxList)-1) + ' in block ' + str(blockHeight))

        conflicts = set(currPool) - set(newPool)
      
        for block in blocks:
            block.removeConflicts(conflicts)

        model.pushBlocks(blocks)

        if feemodel.config.apprun:
            for block in blocks:
                block.writeHistory()

        return blocks


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

    def writeHistory(self, dbFile=historyFile):
        db = None
        dbExists = os.path.exists(dbFile)
        try:
            db = sqlite3.connect(dbFile)
            if not dbExists:
                with db:
                    db.execute('CREATE TABLE blocks (height INTEGER UNIQUE, size INTEGER, time REAL)')
                    db.execute('CREATE TABLE txs (blockheight INTEGER, txid TEXT, data TEXT)')
            with db:
                db.execute('INSERT INTO blocks VALUES (?,?,?)', (self.height, self.size, self.time))
                db.executemany('INSERT INTO txs VALUES (?,?,?)',
                    [(self.height, txid, json.dumps(entry,default=decimalDefault))
                    for txid,entry in self.entries.iteritems()])
                historyLimit = self.height - keepHistory
                if keepHistory > 0:
                    db.execute('DELETE FROM blocks WHERE height<=?', (historyLimit,))
                    db.execute('DELETE FROM txs WHERE blockheight<=?', (historyLimit,))
        except Exception as e:
            logWrite(repr(e))
            logWrite("Exception in writing/cleaning history.")
        finally:
            if db:
                db.close()

    @classmethod
    def blockFromHistory(cls, blockHeight, dbFile=historyFile):
        db = None
        try:
            db = sqlite3.connect(dbFile)
            block = db.execute('SELECT size,time FROM blocks WHERE height=?',
                (blockHeight,)).fetchall()
            if block:
                blockSize,blockTime = block[0]
            else:
                return None
            txlist = db.execute('SELECT txid,data FROM txs WHERE blockheight=?', (blockHeight,))
            entries = {txid: json.loads(str(data)) for txid,data in txlist}
            for entry in entries.itervalues():
                entry['fee'] = decimal.Decimal(entry['fee'])
                entry['startingpriority'] = decimal.Decimal(entry['startingpriority'])
                entry['currentpriority'] = decimal.Decimal(entry['currentpriority'])
            return cls(entries,blockHeight,blockSize,blockTime)
        except Exception as e:
            logWrite(repr(e))
            return None
        finally:
            if db:
                db.close()

    def __eq__(self,other):
        if not isinstance(other,Block):
            return False
        return all([
            self.entries == other.entries,
            self.height == other.height,
            self.size == other.size,
            self.time == other.time,
        ])


def decimalDefault(obj):
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    raise TypeError

