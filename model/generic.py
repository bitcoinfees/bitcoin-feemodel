class FeeTx:
    def __init__(self, feeTuple):
        self.feeRate = feeTuple[0]
        self.inBlock = bool(feeTuple[1])
        self.size = feeTuple[2]
        self.priority = feeTuple[3]

    def __repr__(self):
        return "Tx(feerate: %d, inblock: %d, size: %d" % (
            self.feeRate, self.inBlock, self.size)

class PriorityTx:
    def __init__(self, priorityTuple):
        self.priority = priorityTuple[0]
        self.inBlock = bool(priorityTuple[1])
        self.size = priorityTuple[2]
        self.feeRate = priorityTuple[3]

    def __repr__(self):
        return "Tx(priority %.1f, inblock: %d, discount %d, cumsize: %d)" % (
            self.priority, self.inBlock, self.discounted, self.cumSize)