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
from feemodel.util import proxy, logWrite, StoppableThread

pollPeriod = config['pollPeriod']
keepHistory = config['keepHistory']
historyLock = threading.Lock()

class TxMempool(StoppableThread):
    # Have to handle RPC errors
    # Writehistory means write to db the mempool state at each block.
    # We keep <keepHistory> number of past blocks.

    def update(self):
        currHeight, mapTxNew = proxy.pollMempool()
        if currHeight > self.bestSeenBlock:
            threading.Thread(target=self.processBlocks,
                args=(range(self.bestSeenBlock+1,currHeight+1),
                    self.mapTx, deepcopy(mapTxNew)),
                name='mempool-processBlocks').start()
            self.mapTx = mapTxNew
            self.bestSeenBlock = currHeight
            return True
            # Be careful here: may have to pass deepcopy of self.mapTx to processBlocks,
            # if we are going to return self.mapTx for other functions to use.
        else:
            self.mapTx = mapTxNew
            return False

    def run(self):
        feemodel.config.apprun = True
        logWrite("Starting mempool")
        self.bestSeenBlock, self.mapTx = proxy.pollMempool()
        while not self.isStopped():
            self.update()
            self.sleep(pollPeriod)
        logWrite("Closing up mempool...")
        for thread in threading.enumerate():
            if thread.name.startswith('mempool'):
                thread.join()
        logWrite("Finished everything.")

    @staticmethod
    def processBlocks(blockHeightRange, currPool, newPool, blockTime=None):
        with historyLock:
            if not blockTime:
                blockTime = int(time())
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
                    entry['isConflict'] = False

                blocks.append(Block(entries,blockHeight,blockSize,blockTime))
                logWrite('%d of %d in block %d' % (
                    numMempoolTxsInBlock, len(blockTxList)-1, blockHeight))

            # To-do: insert warnings if block inclusion ratio is too low, or conflicts are too high
            conflicts = set(currPool) - set(newPool)

            numConflicts = 0
            for block in blocks:
                numConflicts += block.removeConflicts(conflicts)

            if numConflicts:
                logWrite("%d conflicts removed." % numConflicts)

            if feemodel.config.apprun:
                for block in blocks:
                    block.writeHistory()

            return blocks


class Block(object):
    def __init__(self, entries, blockHeight, blockSize, blockTime):
        # To-do: add a 'tx-coverage' field
        self.entries = entries
        self.height = blockHeight
        self.size = blockSize
        self.time = blockTime

    def removeConflicts(self, conflicts):
        numConflicts = 0
        for txid in self.entries.keys():
            if txid in conflicts:
                self.entries[txid]['isConflict'] = True
                numConflicts += 1

        return numConflicts

    def writeHistory(self, dbFile=historyFile):
        db = None
        dbExists = os.path.exists(dbFile)
        try:
            db = sqlite3.connect(dbFile)
            if not dbExists:
                with db:
                    db.execute('CREATE TABLE blocks (height INTEGER UNIQUE, size INTEGER, time REAL)')
                    db.execute('CREATE TABLE txs (blockheight INTEGER, txid TEXT, data TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS heightidx ON txs (blockheight)')
            with db:
                db.execute('INSERT INTO blocks VALUES (?,?,?)', (self.height, self.size, self.time))
                db.executemany('INSERT INTO txs VALUES (?,?,?)',
                    [(self.height, txid, json.dumps(entry,default=decimalDefault))
                    for txid,entry in self.entries.iteritems()])
            historyLimit = self.height - keepHistory
            if keepHistory > 0:
                with db:
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


class LoadHistory(object):
    def __init__(self, dbFile=historyFile):
        self.fns = []
        self.dbFile = dbFile

    def registerFn(self, fn, blockHeightRange):
        # blockHeightRange tuple (start,end) includes start but not end, to adhere to range() convention
        self.fns.append((fn, blockHeightRange))

    def loadBlocks(self):
        startHeight = min([f[1][0] for f in self.fns])
        endHeight = max([f[1][1] for f in self.fns])

        for height in range(startHeight, endHeight):
            block = Block.blockFromHistory(height, self.dbFile)
            for fn, blockHeightRange in self.fns:
                if height >= blockHeightRange[0] and height < blockHeightRange[1]:
                    fn([block])

def decimalDefault(obj):
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    raise TypeError

