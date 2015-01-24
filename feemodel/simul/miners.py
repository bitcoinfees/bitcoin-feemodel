from random import expovariate


class SimpleMiner(object):
    def __init__(self, maxblocksize, minfeerate):
        self.maxblocksize = maxblocksize
        self.minfeerate = minfeerate
        self.blockrate = 1./600

    def next_block_policy(self):
        blockinterval = expovariate(self.blockrate)
        return blockinterval, self.maxblocksize, self.minfeerate

    def calc_capacities(self, tx_source):
        feerates = [0, self.minfeerate]
        tx_byterates = tx_source.get_byterates(feerates)
        caps = [0., self.blockrate*self.maxblocksize]

        return feerates, tx_byterates, caps

    def get_feepoints(self):
        return [self.minfeerate]
