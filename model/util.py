import os
from bitcoin.rpc import Proxy
from time import ctime
import cPickle as pickle
from model.config import dbFile
import sqlite3

proxy = Proxy()
logPath = os.path.join(os.path.dirname(__file__), '../debug.log')

def logWrite(entry, toStdOut=True):
    '''An entry in the log file.'''
    # if not currHeight:
    #     currHeight = proxy.getblockcount()
    s = ctime()+' ' + entry
    if toStdOut:
        print s
    with open(logPath, 'a') as logFile:
        logFile.write(s + '\n')

def pickleLoad(path):
    with open(path, 'rb') as f:
        data = pickle.load(f)
    return data

def getFees(blockHeight, db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    try:
        fees = c.execute('SELECT feerate, inblock FROM fees WHERE blockheight=?', (blockHeight,)).fetchall()
        return fees
    finally:
        if 'localdb' in locals():
            localdb.close()

def getBlocks(db=None):
    if db is None:
        localdb = sqlite3.connect(dbFile)
        c = localdb.cursor()
    else:
        c = db.cursor()

    try:
        blocks = c.execute('SELECT blockheight FROM block').fetchall()
        return [block[0] for block in blocks]
    finally:
        if 'localdb' in locals():
            localdb.close()
