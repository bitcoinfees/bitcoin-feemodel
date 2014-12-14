import threading
from feemodel.txmempool import TxMempool

class Model(object):
    def __init__(self):
        self.mempool = None
        self.modelLock = threading.Lock()
        self.pushBlocks = PushBlocks(self.modelLock)
        self.blocksToConfirm = ModelInterface(self.modelLock)
        self.feeToConfirm = ModelInterface(self.modelLock)
        self.miscStats = ModelInterface(self.modelLock)

    def start(self):        
        @self.pushBlocks.decorateProcessBlocks()
        TxMempool.processBlocks
        logWrite("Starting mempool.")
        self.mempool = TxMempool()
        self.mempool.start()

    def stop(self):
        if self.mempool:
            self.mempool.stop()



class ModelInterface(object):
    def __init__(self,lock):
        self.lock = lock
        self.fns = []

    def register(self):
        def decorator(fn):
            if not fn in self.fns:
                self.fns.append(fn)
            return fn
        return decorator

    def __call__(self, *args, **kwargs):
        with self.lock:
            return [fn(*args, **kwargs) for fn in self.fns]

class PushBlocks(ModelInterface):
    def decorateProcessBlocks(self):
        def decorator(processBlocks):
            def processBlocksPush(*args, **kwargs):
                blocks = processBlocks(*args,**kwargs)
                self(blocks)
                return blocks
            return processBlocksPush
        return decorator


