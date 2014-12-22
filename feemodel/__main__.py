from feemodel.nonparam import NonParam
from feemodel.model import Model
from feemodel.txmempool import LoadHistory
from feemodel.queue import QEOnline
from feemodel.measurement import WaitMeasure
from feemodel.util import proxy
from pprint import pprint
from time import sleep
from flask import Flask
import json

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
model = Model()

app = Flask(__name__)
@app.route('/txstats')
def txstats():
    return json.dumps(model.getStats())

lh = LoadHistory()
nonparam = NonParam()
qe = QEOnline(60000,2016)
wm = WaitMeasure(60000,2016)

currHeight = proxy.getblockcount()
lh.registerFn(lambda x: qe.pushBlocks(x,True), (max(currHeight-2016,qe.bestHeight), currHeight+10))
lh.registerFn(lambda x: wm.pushBlocks(x,True), (max(currHeight-2016,wm.bestHeight), currHeight+10))

model.pushBlocks.register(nonparam.pushBlocks)
model.pushBlocks.register(qe.pushBlocks)
model.pushBlocks.register(wm.pushBlocks)

model.getStats.register(qe.getStats)
model.getStats.register(wm.getStats)

lh.loadBlocks()
qe.adaptiveCalc()
qe.saveBlockData()
wm.adaptiveCalc()
try:
    wm.saveBlockData()
except IOError:
    print("io error")

model.start()

try:
    app.run(port=5001)
finally:
    model.stop()
    model.join()
    print("Exiting program.")
