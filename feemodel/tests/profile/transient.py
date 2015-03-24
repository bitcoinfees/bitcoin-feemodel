import cProfile
from feemodel.txmempool import MemBlock
from feemodel.simul.transient import transientsim
from feemodel.simul import Simul, SimEntry
from feemodel.util import load_obj

dbfile = '../data/test.db'
refpools = load_obj('../data/pe_ref.pickle')
reftxsource = load_obj('../data/tr_ref.pickle')

b = MemBlock.read(333931, dbfile=dbfile)
init_entries = [SimEntry.from_mementry(txid, entry)
                for txid, entry in b.entries.items()]

sim = Simul(refpools, reftxsource)
# waittimes, realtime, numiters = transientsim(sim, init_entries=init_entries)
cProfile.run("waittimes, realtime, numiters = transientsim("
             "sim, init_entries=init_entries, multiprocess=None)")
print("Completed in {}s with {} iters.".format(realtime, numiters))
print("Feerate\tMean wait")
for feerate, waitdata in sorted(waittimes.items()):
    waitdata.calc_stats()
    print("{}\t{}".format(feerate, waitdata.mean))
sim.cap.print_cap()
