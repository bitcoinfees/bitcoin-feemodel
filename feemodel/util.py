from bitcoin.rpc import Proxy
from config import logFile, config
from time import ctime

proxy = Proxy()
toStdOut = config['logging']['toStdOut']

def logWrite(entry):
    s = ctime() + ': ' + entry
    if toStdOut:
        print s
    with open(logFile, 'a') as f:
        f.write(s + '\n')

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




