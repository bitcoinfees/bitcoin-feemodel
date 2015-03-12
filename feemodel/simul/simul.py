from __future__ import division

from collections import defaultdict
from bisect import insort
from time import time

from feemodel.simul.stats import Capacity

rate_ratio_thresh = 0.9


class Simul(object):
    def __init__(self, pools, tx_source):
        self.pools = pools
        self.tx_source = tx_source
        # TODO: check edge conditions for feerates
        # feerates, cap_lower, cap_upper = self.pools.get_capacity()
        # tx_byterates = tx_source.get_byterates(feerates)
        # self.cap = Capacity(feerates, tx_byterates, cap_lower, cap_upper,
        #                     tx_source.txrate)
        self.cap = Capacity(pools, tx_source)
        self.stablefeerate = self.cap.calc_stablefeerate(rate_ratio_thresh)
        if self.stablefeerate is None:
            raise ValueError("The queue is not stable - arrivals exceed "
                             "processing for all feerates.")
        self.mempool = None

    def run(self, init_entries=None):
        if init_entries is None:
            init_entries = []
        self.mempool = SimMempool(init_entries)
        starttime = time()
        for simblock in self.pools.blockgen():
            elapsedrealtime = time() - starttime
            newentries = self.tx_source.generate_txs(simblock.interval)
            newentries = filter(
                lambda entry: entry.tx.feerate >= self.stablefeerate,
                newentries)
            self.mempool._add_entries(newentries)
            self.mempool._process_block(simblock)
            simblock.sfr = max(simblock.sfr, self.stablefeerate)

            yield simblock, elapsedrealtime


class SimMempool(object):
    def __init__(self, init_entries):
        self._nodeps = []
        self._havedeps = {}
        self._depmap = defaultdict(list)

        txids = [entry._id for entry in init_entries]
        # Assert that there are no duplicate txids.
        assert len(set(txids)) == len(txids)
        for entry in init_entries:
            if not entry.depends:
                self._nodeps.append(entry)
            else:
                self._havedeps[entry._id] = entry
                for dep in entry.depends:
                    # Assert that there are no hanging dependencies
                    assert dep in txids
                    self._depmap[dep].append(entry._id)

        # For resetting the mempool to initial state.
        self._nodeps_bak = self._nodeps[:]
        self._havedeps_bak = {
            txid: entry for txid, entry in self._havedeps.items()}

    @property
    def entries(self):
        return self._nodeps + self._havedeps.values()

    def reset(self):
        self._nodeps = self._nodeps_bak[:]
        self._havedeps = {
            txid: entry for txid, entry in self._havedeps_bak.items()}
        for entry in self._havedeps.values():
            entry._reset_deps()

    def _add_entries(self, newentries):
        self._nodeps.extend(newentries)

    def _process_block(self, simblock):
        maxblocksize = simblock.poolinfo[1].maxblocksize
        minfeerate = simblock.poolinfo[1].minfeerate
        blocksize = 0
        sfr = float("inf")
        blocksize_ltd = 0

        self._nodeps.sort(key=lambda entry: entry.tx.feerate)
        rejected_entries = []
        blocktxs = []
        while self._nodeps:
            newentry = self._nodeps.pop()
            if newentry.tx.feerate >= minfeerate:
                newblocksize = newentry.tx.size + blocksize
                if newblocksize <= maxblocksize:
                    if blocksize_ltd > 0:
                        blocksize_ltd -= 1
                    else:
                        sfr = min(newentry.tx.feerate, sfr)

                    blocktxs.append(newentry.tx)
                    blocksize = newblocksize

                    dependants = self._depmap.get(newentry._id)
                    if dependants:
                        for txid in dependants:
                            entry = self._havedeps[txid]
                            entry.depends.remove(newentry._id)
                            if not entry.depends:
                                insort(self._nodeps, entry)
                                del self._havedeps[txid]
                else:
                    rejected_entries.append(newentry)
                    blocksize_ltd += 1
            else:
                rejected_entries.append(newentry)
                break
        self._nodeps.extend(rejected_entries)

        simblock.sfr = sfr if blocksize_ltd else minfeerate
        simblock.is_sizeltd = bool(blocksize_ltd)
        simblock.size = blocksize
        simblock.txs = blocktxs
