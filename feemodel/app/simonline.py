import logging
import threading
import os
from copy import deepcopy
from feemodel.config import datadir
from feemodel.util import save_obj, load_obj, StoppableThread, proxy
from feemodel.txmempool import TxMempool
from feemodel.estimate.pools import PoolsEstimator
from feemodel.estimate.txrate import TxRateEstimator

logger = logging.getLogger(__name__)


class SimulOnline(TxMempool):
    def __init__(self):
        super(self.__class__, self).__init__()












