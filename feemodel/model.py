import threading
from feemodel.txmempool import TxMempool
import feemodel.config

class Model(TxMempool):
    def __init__(self):
        super(Model, self).__init__()
        self.modelLock = threading.Lock()
        self.pushBlocks = ModelInterface(self.modelLock)
        self.estimateFee = ModelInterface(self.modelLock)
        self.estimateTx = ModelInterface(self.modelLock)
        self.getStats = ModelInterface(self.modelLock)

    def processBlocks(self, *args, **kwargs):
        blocks = super(Model, self).processBlocks(*args,**kwargs)
        self.pushBlocks(blocks)
        return blocks


class ModelInterface(object):
    def __init__(self,lock):
        self.lock = lock
        self.fns = []

    def register(self, fn):
        if not fn in self.fns:
            self.fns.append(fn)

    def __call__(self, *args, **kwargs):
        with self.lock:
            return [fn(*args, **kwargs) for fn in self.fns]


class ModelError(Exception):
    pass



