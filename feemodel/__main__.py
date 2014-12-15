from feemodel.nonparam import NonParam
from feemodel.model import Model
from time import sleep

# feemodel.config.apprun = True

# model = DummyModel()
# mempool = TxMempool(model,writeHistory=True)
# mempoolThread = TxMempoolThread(mempool)
# mempoolThread.start()

# try:
#     while True:
#         # print('At block ' + str(mempool.bestSeenBlock))
#         print('.'),
#         sleep(60)
# except KeyboardInterrupt:
#     print("keyboard.")
# finally:
#     mempoolThread.stop()
#     mempoolThread.join()
#     print("Finished everything.")

# ===========

nonparam = NonParam()
model = Model()

model.pushBlocks.register(nonparam.pushBlocks)
model.start()

try:
    while True:
        # print('At block ' + str(mempool.bestSeenBlock))
        print('.'),
        sleep(60)
except KeyboardInterrupt:
    print("keyboard.")
finally:
    model.stop()
    model.join()
    print("Finished everything.")
