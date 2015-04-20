import cProfile
from feemodel.txmempool import MemBlock
from feemodel.simul.transient import transientsim
from feemodel.simul import Simul
from feemodel.util import DataSample
from feemodel.tests.config import memblock_dbfile as dbfile, poolsref, txref

# flake8: noqa

poolsref.print_pools()
init_entries = MemBlock.read(333931, dbfile=dbfile).entries
sim = Simul(poolsref, txref)

print("Starting transientsim.")
cProfile.run("feepoints, waittimes, realtime, numiters = transientsim("
             "sim, init_entries=init_entries, "
             "numprocesses=None, stopflag=stopflag)")
print("Completed in {}s with {} iters.".format(realtime, numiters))

print("Feerate\tMean wait")
for feerate, waitsample in zip(feepoints, waittimes):
    waitdata = DataSample(waitsample)
    waitdata.calc_stats()
    print("{}\t{}".format(feerate, waitdata.mean))
sim.cap.print_cap()
