from bisect import insort


def _process_block(_nodeps, _havedeps, _depmap, simblock):
    maxblocksize = simblock.poolinfo[1].maxblocksize
    minfeerate = simblock.poolinfo[1].minfeerate
    blocksize = 0
    sfr = float("inf")
    blocksize_ltd = 0

    _nodeps.sort()
    # _nodeps.sort(key=lambda entry: entry.tx.feerate)
    rejected_entries = []
    blocktxs = []
    while _nodeps:
        # newentry = _nodeps.pop()
        newtx = _nodeps.pop()
        # if newentry.tx.feerate >= minfeerate:
        if newtx[0] >= minfeerate:
            # newblocksize = newentry.tx.size + blocksize
            newblocksize = newtx[1] + blocksize
            if newblocksize <= maxblocksize:
                if blocksize_ltd > 0:
                    blocksize_ltd -= 1
                else:
                    # sfr = min(newentry.tx.feerate, sfr)
                    if newtx[0] < sfr:
                        sfr = newtx[0]

                # blocktxs.append(newentry.tx)
                blocktxs.append(newtx)
                blocksize = newblocksize

                # dependants = _depmap.get(newentry._id)
                dependants = _depmap.get(newtx[2])
                if dependants:
                    for txid in dependants:
                        entry = _havedeps[txid]
                        # entry.depends.remove(newentry._id)
                        entry[1].remove(newtx[2])
                        # if not entry.depends:
                        if not entry[1]:
                            insort(_nodeps, entry[0])
                            del _havedeps[txid]
            else:
                rejected_entries.append(newtx)
                blocksize_ltd += 1
        else:
            rejected_entries.append(newtx)
            break
    _nodeps.extend(rejected_entries)

    simblock.sfr = sfr if blocksize_ltd else minfeerate
    simblock.is_sizeltd = bool(blocksize_ltd)
    simblock.size = blocksize
    simblock.txs = blocktxs
