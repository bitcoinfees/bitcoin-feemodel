from bitcoin.rpc import Proxy, JSONRPCException
import feemodel.config
from feemodel.config import logFile, config, historyFile
from time import ctime
import sqlite3
import threading
from pprint import pprint

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


proxy = BatchProxy()
toStdOut = config['logging']['toStdOut']




