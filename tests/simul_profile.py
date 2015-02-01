import cProfile
from feemodel.txmempool import MemEntry
from feemodel.simul import Simul
from feemodel.simul.stats import steadystate
from simul_test import pools, tx_source, entries

sim = Simul(pools, tx_source)


def basicsim():
    for simblock in sim.run(maxiters=10000):
        pass


print("Basic sim:\n====================")
cProfile.run("basicsim()")
print("Steadystate:\n====================")
cProfile.run("steadystate(pools, tx_source, maxiters=10000)")
