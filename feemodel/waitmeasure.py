"""Measure transaction wait times within a certain block range."""
from __future__ import division

from feemodel.txmempool import MemBlock, MEMBLOCK_DBFILE


def waitmeasure(startheight, endheight, dbfile=MEMBLOCK_DBFILE):
    blacklist = set()
    txs = []
    for height in range(startheight, endheight+1):
        block = MemBlock.read(height, dbfile=dbfile)
        if block is None:
            continue
        # Don't consider txs with high priority or those with mempool deps.
        blacklist.update(set([
            txid for txid, entry in block.entries.items()
            if entry.is_high_priority() or entry.depends
        ]))
        txs.extend([
            (entry.feerate, block.time - entry.time)
            for txid, entry in block.entries.items()
            if txid not in blacklist and entry.inblock
        ])
        blacklist = blacklist & set(block.entries)

    return txs
