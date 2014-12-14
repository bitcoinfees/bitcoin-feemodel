import feemodel.config
from feemodel.nonparam import NonParam
from feemodel.util import DummyModel
from feemodel.txmempool import TxMempoolThread, TxMempool
import threading
from time import sleep

feemodel.config.apprun = True

model = DummyModel()
mempool = TxMempool(model,writeHistory=True)
mempoolThread = TxMempoolThread(mempool)
mempoolThread.start()

try:
    while True:
        # print('At block ' + str(mempool.bestSeenBlock))
        print('.'),
        sleep(60)
except KeyboardInterrupt:
    print("keyboard.")
finally:
    mempoolThread.stop()
    mempoolThread.join()
    print("Finished everything.")