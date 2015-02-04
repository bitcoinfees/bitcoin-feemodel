import threading
import logging
from time import time

from feemodel.util import StoppableThread, DataSample, proxy, Table
from feemodel.simul.simul import get_feeclasses
from feemodel.simul import Simul, SimTx
from feemodel.estimate import TxRateEstimator

tx_maxsamplesize = 10000
default_update_period = 60.
default_maxiters = 10000
default_maxtime = 60.

logger = logging.getLogger(__name__)


class TransientOnline(StoppableThread):
    def __init__(self, mempool, peo, window,
                 update_period_secs=default_update_period,
                 maxiters=default_maxiters, maxtime=default_maxtime):
        self.stats_lock = threading.Lock()
        self.mempool = mempool
        self.peo = peo
        self.window = window
        self.update_period = update_period_secs
        self.maxiters = maxiters
        self.maxtime = maxtime
        self.stats = TransientStats()
        super(self.__class__, self).__init__()

    def run(self):
        logger.info("Starting transient online sim.")
        try:
            while not self.is_stopped():
                self.update()
                updatetimediff = time() - self.stats.time
                time_till_next = max(0, self.update_period - updatetimediff)
                self.sleep(time_till_next)
        except StopIteration:
            pass
        logger.info("Stopped transient online sim.")

    def update(self):
        currheight = proxy.getblockcount()
        blockrangetuple = (currheight-self.window+1, currheight+1)
        tx_source = TxRateEstimator(maxsamplesize=tx_maxsamplesize)
        tx_source.start(blockrangetuple, stopflag=self.get_stop_object())

        pools = self.peo.pe
        if not pools.get_numpools():
            logger.debug("No pools.")
            return
        sim = Simul(pools, tx_source)
        feeclasses = get_feeclasses(sim.cap, tx_source, sim.stablefeerate)
        try:
            self.simulate(sim, feeclasses)
        except ValueError:
            logger.exception('Exception in transient sim')

    def simulate(self, sim, feeclasses):
        stats = TransientStats()
        stats.time = time()
        init_mempool = [SimTx.from_mementry(txid, entry)
                        for txid, entry in self.mempool.get_entries().items()]
        mempoolsize = sum([tx.size for tx in init_mempool])

        tstats = {feerate: DataSample() for feerate in feeclasses}
        simtime = 0.
        stranded = set(feeclasses)
        numiters = 0
        for block, realtime in sim.run(mempool=init_mempool,
                                       maxiters=float("inf"),
                                       maxtime=self.maxtime):
            simtime += block.interval
            stranding_feerate = block.sfr

            for feerate in list(stranded):
                if feerate >= stranding_feerate:
                    tstats[feerate].add_datapoints([simtime])
                    stranded.remove(feerate)

            if not stranded:
                numiters += 1
                if numiters >= self.maxiters:
                    break
                else:
                    simtime = 0.
                    stranded = set(feeclasses)
                    sim.mempool.reset()
        logger.info("Finished transient simulation in %.2fs and "
                    "%d iterations - mempool size was %d bytes" %
                    (realtime, block.height+1, mempoolsize))

        for feerate, twait in tstats.items():
            if twait.n > 1:
                twait.calc_stats()
            else:
                # Something very bad happened - not likely
                raise ValueError("Only 1 iteration was performed.")
        stats.tstats = tstats
        stats.cap = sim.cap
        stats.stablefeerate = sim.stablefeerate
        stats.mempoolsize = mempoolsize
        self.stats = stats

    @property
    def stats(self):
        with self.stats_lock:
            return self._stats

    @stats.setter
    def stats(self, val):
        with self.stats_lock:
            self._stats = val


class TransientStats(object):
    def __init__(self):
        self.tstats = None
        self.cap = None
        self.time = 0.
        self.stablefeerate = None

    def print_stats(self):
        titems = sorted(self.tstats.items())
        table = Table()
        table.add_row(('Feerate', 'Avgwait', 'Error'))
        for feerate, twait in titems:
            table.add_row((
                feerate,
                '%.2f' % twait.mean,
                '%.2f' % (twait.mean_interval[1] - twait.mean)
            ))
        table.print_table()
