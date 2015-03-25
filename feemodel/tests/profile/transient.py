import cPickle as pickle
import cProfile
import multiprocessing
from threading import Timer
from feemodel.txmempool import MemBlock
from feemodel.simul.transient import transient_multiproc as transientsim
from feemodel.simul import Simul, SimEntry
from feemodel.util import load_obj, DataSample

dbfile = '../data/test.db'
refpools = load_obj('../data/pe_ref.pickle')
reftxsource = load_obj('../data/tr_ref.pickle')
refpools.print_pools()

b = MemBlock.read(333931, dbfile=dbfile)
init_entries = [SimEntry.from_mementry(txid, entry)
                for txid, entry in b.entries.items()]

sim = Simul(refpools, reftxsource)
stopflag = multiprocessing.Event()

# try:
#     pickle.dumps(sim)
# except Exception as e:
#     print("Cannot pickle sim.")
#     raise e
# else:
#     print("Pickling OK.")
# Timer(2, stopflag.set).start()
# waittimes, realtime, numiters = transientsim(
#     sim, init_entries=init_entries, stopflag=stopflag)
print("Starting transientsim.")
cProfile.run("waittimes, realtime, numiters = transientsim("
             "sim, init_entries=init_entries, multiprocess=None, stopflag=stopflag)")
print("Completed in {}s with {} iters.".format(realtime, numiters))

print("Feerate\tMean wait")
for feerate, waitsample in sorted(waittimes.items()):
    waitdata = DataSample(waitsample)
    waitdata.calc_stats()
    print("{}\t{}".format(feerate, waitdata.mean))
sim.cap.print_cap()
