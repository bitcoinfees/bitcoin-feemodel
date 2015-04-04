from __future__ import division

from collections import defaultdict
from copy import copy
from bisect import insort

from feemodel.simul.stats import Capacity
from feemodel.simul.txsources import SimTx

rate_ratio_thresh = 0.9


cdef class Simul(object):

    cdef readonly object pools, tx_source, cap, stablefeerate
    cdef public SimMempool mempool

    def __init__(self, pools, tx_source):
        self.pools = pools
        self.tx_source = tx_source
        # TODO: check edge conditions for feerates
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
        for simblock in self.pools.blockgen():
            newtxs = self.tx_source.generate_txs(simblock.interval)
            newtxs = filter(lambda tx: tx[0] >= self.stablefeerate, newtxs)
            self.mempool._add_txs(newtxs)
            self.mempool._process_block(simblock)
            simblock.sfr = max(simblock.sfr, self.stablefeerate)

            yield simblock


cdef class SimMempool(object):

    cdef:
        list _nodeps, _nodeps_bak
        dict _havedeps, _havedeps_bak, _depmap

    def __init__(self, init_entries):
        self._nodeps = []
        self._havedeps = {}
        _depmap = defaultdict(list)

        txids = [entry.txid for entry in init_entries]
        # Assert that there are no duplicate txids.
        assert len(set(txids)) == len(txids)
        for entry in init_entries:
            tx = (entry.tx.feerate, entry.tx.size, entry.txid)
            if not entry.depends:
                self._nodeps.append(tx)
            else:
                self._havedeps[entry.txid] = (tx, entry.depends)
                for dep in entry.depends:
                    # Assert that there are no hanging dependencies
                    assert dep in txids
                    _depmap[dep].append(entry.txid)

        # For resetting the mempool to initial state.
        self._nodeps_bak = self._nodeps[:]
        self._havedeps_bak = copy(self._havedeps)
        self._depmap = dict(_depmap)

    @property
    def entries(self):
        entries = []
        entries.extend(
            [SimEntry(tx[2], SimTx(tx[0], tx[1])) for tx in self._nodeps])
        entries.extend(
            [SimEntry(txid, SimTx(entry[0][0], entry[0][1]), entry[1])
             for txid, entry in self._havedeps.items()])
        return entries

    def reset(self):
        cdef SimDepends depends
        self._nodeps = self._nodeps_bak[:]
        self._havedeps = copy(self._havedeps_bak)
        for _dum, depends in self._havedeps.itervalues():
            depends.reset()

    cdef _add_txs(self, list newtxs):
        self._nodeps.extend(newtxs)

    cdef _process_block(self, simblock):
        DEF MAXFEE = 2100000000
        cdef:
            int maxblocksize, minfeerate, blocksize, sfr, blocksize_ltd
            int newtxfeerate, newtxsize
            tuple newtx, deptx
            list dependants
            SimDepends depends
        local_insort = insort

        _poolmfr = simblock.poolinfo[1].minfeerate
        minfeerate = _poolmfr if _poolmfr != float("inf") else MAXFEE
        maxblocksize = simblock.poolinfo[1].maxblocksize
        sfr = MAXFEE
        blocksize = 0
        blocksize_ltd = 0

        self._nodeps.sort()
        rejected_entries = []
        blocktxs = []
        while self._nodeps:
            newtx = self._nodeps.pop()
            newtxfeerate, newtxsize, newtxid = newtx
            if newtxfeerate >= minfeerate:
                newblocksize = newtxsize + blocksize
                if newblocksize <= maxblocksize:
                    if blocksize_ltd > 0:
                        blocksize_ltd -= 1
                    else:
                        # FIXME: SFR setting must be changed to match
                        #        stranding.py's definition
                        if newtxfeerate < sfr:
                            sfr = newtxfeerate

                    blocktxs.append(newtx)
                    blocksize = newblocksize

                    dependants = self._depmap.get(newtxid)
                    if dependants is not None:
                        for txid in dependants:
                            deptx, depends = self._havedeps[txid]
                            depends.remove(newtxid)
                            if not depends:
                                local_insort(self._nodeps, deptx)
                                del self._havedeps[txid]
                else:
                    rejected_entries.append(newtx)
                    blocksize_ltd += 1
            else:
                rejected_entries.append(newtx)
                break
        # Reverse makes the sorting a bit faster
        rejected_entries.reverse()
        self._nodeps.extend(rejected_entries)

        simblock.sfr = sfr + 1 if blocksize_ltd else minfeerate
        simblock.is_sizeltd = bool(blocksize_ltd)
        simblock.size = blocksize
        simblock.txs = blocktxs


class SimEntry(object):
    def __init__(self, txid, simtx, depends=None):
        self.txid = txid
        self.tx = simtx
        if isinstance(depends, SimDepends):
            self.depends = depends
        else:
            self.depends = SimDepends(depends)

    @classmethod
    def from_mementry(cls, txid, entry):
        return cls(txid, SimTx(entry.feerate, entry.size),
                   depends=entry.depends)

    def __repr__(self):
        return "SimEntry({}, {}, {})".format(
            self.txid, repr(self.tx), repr(self.depends))


cdef class SimDepends(object):

    cdef set _depends, _depends_bak

    def __init__(self, depends):
        self._depends = set(depends) if depends else set()
        self._depends_bak = set(self._depends)

    cdef remove(self, dependency):
        self._depends.remove(dependency)
        return bool(self._depends)

    cdef reset(self):
        self._depends = set(self._depends_bak)

    def repr(self):
        return "SimDepends({})".format(self._depends)

    def __iter__(self):
        return iter(self._depends)

    def __nonzero__(self):
        return bool(self._depends)
