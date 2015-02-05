import cProfile
from feemodel.txmempool import MemEntry
from feemodel.simul import Simul
from feemodel.simul.stats import steadystate, transient
from simul_test import pools, tx_source, init_mempool, tx_source_copy

sim = Simul(pools, tx_source)
# this is veryyyy slow
# simcopy = Simul(pools, tx_source_copy)


def basicsim(sim):
    for simblock in sim.run(maxiters=10000):
        pass


# print("Basic sim:\n====================")
# cProfile.run("basicsim(sim)")
# print("Basic sim with copy:\n====================")
# cProfile.run("basicsim(simcopy)")
print("Steadystate:\n====================")
cProfile.run("steadystate(pools, tx_source, maxiters=10000)")
print("Transient:\n====================")
cProfile.run("transient(init_mempool, pools, tx_source, maxiters=10000)")

