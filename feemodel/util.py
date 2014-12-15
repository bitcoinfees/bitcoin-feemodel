from bitcoin.rpc import Proxy
import feemodel.config
from feemodel.config import logFile, config, historyFile
from time import ctime
import sqlite3
import threading

class BlockingProxy(Proxy):
    '''
    Thread-safe version of Proxy
    '''
    def __init__(self):
        super(BlockingProxy, self).__init__()
        self.lock = threading.Lock()

    def _call(self, *args):
        with self.lock:
            return super(BlockingProxy, self)._call(*args)

def logWrite(entry):
    s = ctime() + ': ' + entry
    if feemodel.config.apprun:
        with open(logFile, 'a') as f:
            f.write(s + '\n')
    if toStdOut or not feemodel.config.apprun:
        print(s)

def getHistory(dbFile=historyFile):
    db = None
    try:
        db = sqlite3.connect(dbFile)
        blocks = db.execute('SELECT * FROM blocks').fetchall()
        return blocks
    finally:
        if db:
            db.close()


proxy = BlockingProxy()
toStdOut = config['logging']['toStdOut']




class DummyModel(object):
    def __init__(self):
        pass

    def pushBlocks(self, blocks):
        print("I'm a dummy.")

    





def getFees(blockHeight, db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    try:
        fees = c.execute('SELECT feerate, inblock, size FROM fees WHERE blockheight=?',
            (blockHeight,)).fetchall()
        return fees
    finally:
        if 'localdb' in locals():
            localdb.close()

def getPriority(blockHeight, db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    try:
        priority = c.execute('SELECT priority, inblock, size FROM priority WHERE blockheight=?',
            (blockHeight,)).fetchall()
        return priority
    finally:
        if 'localdb' in locals():
            localdb.close()


def getBlocks(minBlock=None, maxBlock=None, db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    if not minBlock:
        minBlock = 0
    if not maxBlock:
        maxBlock = proxy.getblockcount()

    try:
        blocks = c.execute('SELECT blockheight FROM block WHERE blockheight>? AND blockheight<=?',
            (minBlock,maxBlock)).fetchall()
        return [block[0] for block in blocks]
    finally:
        if 'localdb' in locals():
            localdb.close()

def getBlockSize(blockHeight, db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    try:
        blockSize = c.execute('SELECT blocksize FROM block WHERE blockheight=?',
            (blockHeight,)).fetchall()
        return blockSize[0]
    finally:
        if 'localdb' in locals():
            localdb.close()

def getBlockMinTime(blockHeight, db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    try:
        blockSize = c.execute('SELECT mintime FROM block WHERE blockheight=?',
            (blockHeight,)).fetchall()
        return blockSize[0]
    finally:
        if 'localdb' in locals():
            localdb.close()


def getBlockData(startBlock, endBlock, db=None):
    if db is None:
        db = sqlite3.connect(dbFile)
        closeDb = True
    else:
        closeDb = False

    try:
        priority = db.execute('SELECT priority, inblock, size, feeRate, blockheight FROM priority WHERE\
            blockheight BETWEEN ? AND ?', (startBlock,endBlock)).fetchall()
        fees = db.execute('SELECT feeRate, inblock, size, priority, blockheight FROM fees WHERE\
            blockheight BETWEEN ? AND ?', (startBlock,endBlock)).fetchall()
        blockSizes = db.execute('SELECT blockheight, blocksize from block WHERE\
            blockheight BETWEEN ? AND ?', (startBlock,endBlock)).fetchall()
        return fees, priority, blockSizes
    finally:
        if closeDb:
            db.close()




