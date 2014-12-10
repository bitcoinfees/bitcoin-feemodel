from feemodel.txmempool import TxMempool
from feemodel.nonparam import NonParam

model = NonParam()
mempool = TxMempool(model)
mempool.run()