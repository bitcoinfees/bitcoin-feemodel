import cProfile
from feemodel.txmempool import MemEntry
from feemodel.simul import Simul
from feemodel.simul.simul import steadystate, transient
from simul_test import pools, tx_source, rawmempool

entries = {txid: MemEntry(rawentry) for txid, rawentry in rawmempool.items()}
sim = Simul(pools, tx_source)
print("Basic sim:\n====================")
cProfile.run("sim.run(mempool=entries, maxiters=10000)")
print("Steadystate:\n====================")
cProfile.run("steadystate(pools, tx_source, maxiters=10000)")



