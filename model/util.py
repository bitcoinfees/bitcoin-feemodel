import os
from bitcoin.rpc import Proxy
from time import ctime

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