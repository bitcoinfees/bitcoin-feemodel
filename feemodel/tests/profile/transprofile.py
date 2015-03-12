import os
import cProfile
import logging

from feemodel.tests.testproxy import proxy
import feemodel.util
feemodel.util.proxy = proxy
import feemodel.config
feemodel.config.history_file = os.path.abspath('../data/test.db')
from feemodel.tests.testproxy import TestMempool
from feemodel.app import TransientOnline, PoolsEstimatorOnline

logging.basicConfig(level=logging.DEBUG)

mempool = TestMempool()
peo = PoolsEstimatorOnline(2016, update_period=1129600)
peo.pe = feemodel.util.load_obj('../data/testpe.pickle')

trans = TransientOnline(mempool, peo, 18, maxtime=600, maxiters=4000)

cProfile.run('trans.update()')
trans.stats.print_stats()
