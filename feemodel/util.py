from bitcoin.rpc import Proxy, JSONRPCException
from bitcoin.wallet import CBitcoinAddress
import feemodel.config
from feemodel.config import logFile, config, historyFile
from time import ctime
import sqlite3
import threading
from pprint import pprint
from contextlib import contextmanager
from random import random
from functools import wraps

try:
    import cPickle as pickle
except ImportError:
    import pickle

def getCoinbaseInfo(blockHeight=None, block=None):
    if not block:
        block = proxy.getblock(proxy.getblockhash(blockHeight))
    coinbaseTx = block.vtx[0]
    assert coinbaseTx.is_coinbase()
    addr = str(CBitcoinAddress.from_scriptPubKey(coinbaseTx.vout[0].scriptPubKey))
    tag = str(coinbaseTx.vin[0].scriptSig).decode('utf-8', 'ignore')

    return addr, tag

def getBlockTimeStamp(blockHeight):
    block = proxy.getblock(proxy.getblockhash(blockHeight))
    return block.nTime


class StoppableThread(threading.Thread):
    def __init__(self):
        super(StoppableThread, self).__init__()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def isStopped(self):
        return self._stop.is_set()

    def sleep(self, secs):
        '''Sleep but wakeup immediately on stop()'''
        self._stop.wait(timeout=secs)

    @contextmanager
    def threadStart(self):
        self.start()
        yield
        self.stop()
        self.join()

    def getStopObject(self):
        return self._stop


class Saveable(object):
    def __init__(self, saveFile):
        self.saveFile = saveFile

    def saveObject(self):
        with open(self.saveFile, 'wb') as f:
            pickle.dump(self, f)

    @staticmethod
    def loadObject(saveFile):
        with open(saveFile, 'rb') as f:
            obj = pickle.load(f)
        return obj

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class BlockingProxy(Proxy):
    '''
    Thread-safe version of Proxy
    '''
    def __init__(self):
        super(BlockingProxy, self).__init__()
        self.rlock = threading.RLock()

    def _call(self, *args):
        with self.rlock:
            return super(BlockingProxy, self)._call(*args)


class BatchProxy(BlockingProxy):
    def pollMempool(self):
        with self.rlock:
            self._RawProxy__id_count += 1
            rpc_call_list = [
                {
                    'version': '1.1',
                    'method': 'getblockcount',
                    'params': [],
                    'id': self._RawProxy__id_count
                },
                {
                    'version':'1.1',
                    'method': 'getrawmempool',
                    'params': [True],
                    'id': self._RawProxy__id_count
                }
            ]

            responses = self._batch(rpc_call_list)
            for response in responses:
                if response['error']:
                    raise JSONRPCException(response['error'])
                if 'result' not in response:
                    raise JSONRPCException({
                        'code': -343, 'message': 'missing JSON-RPC result'
                    })

            return responses[0]['result'], responses[1]['result']


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

def estimateVariance(x, xbar):
    return float(sum([(x_i - xbar)**2 for x_i in x])) / (len(x) - 1)

def roundRandom(f):
    '''Returns a random integer with expected value equal to f'''
    q, r = divmod(f, 1)
    if random() <= r:
        return int(q+1)
    else:
        return int(q)

def tryWrap(fn):
    '''Decorator to try function and fail gracefully.'''
    @wraps(fn)
    def nicetry(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logWrite(str(e))
    return nicetry

proxy = BatchProxy()
toStdOut = config['logging']['toStdOut']




