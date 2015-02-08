from __future__ import division


class BlockPredict(object):
    def __init__(self, feerates):
        self.feerates = feerates
        self.numtxs = [0]*len(feerates)
        self.num_in = [0]*len(feerates)
        self.ratio = [0.]*len(feerates)

    def __add__(self, other):
        if self.feerates != other.feerates:
            raise ValueError("Feerates of BlockPredict add operands "
                             "must be equal.")
        totaltxs = [self_n + other_n
                    for self_n, other_n in zip(self.numtxs, other.numtxs)]
        totalin = [self_n + other_n
                   for self_n, other_n in zip(self.num_in, other.num_in)]
        ratio = [n / d if d else 0.
                 for n, d in zip(totalin, totaltxs)]
        result = BlockPredict(self.feerates)
        result.numtxs = totaltxs
        result.num_in = totalin
        result.ratio = ratio
        return result


class Predictions(object):
    def __init__(self, feerates, window):
        self.feerates = feerates
        self.window = window
        self.predicts = {}
        self.block_predicts = {}
        self.scores = BlockPredict(self.feerates)

    def update_predictions(self, entries, transientstats):
        new_txids = set(entries) - set(self.predicts)
        for txid in new_txids:
            entry = entries[txid]
            if not entry.depends:
                self.predicts[txid] = transientstats.predict(entry)
            else:
                self.predicts[txid] = None

    def process_block(self, blocks):
        for block in blocks:
            numpredicts = 0
            block_predict = BlockPredict(self.feerates)
            for txid, entry in block.entries.items():
                if entry.inblock:
                    predicted = self.predicts.get(txid)
                    if predicted:
                        is_in = predicted > block.time
                        block_predict.score(entry.feerate, is_in)
                        del self.predicts[txid]
                        numpredicts += 1
            self.block_predicts[block.height] = block_predict
            for height in self.block_predicts.keys():
                if height <= block.height - self.window:
                    del self.block_predicts[height]

            # Remove from predictions those entries that are no longer
            # in the mempool for whatever reason.
            predicts_del = set(self.predicts) - set(block.entries)
            for txid in predicts_del:
                del self.predicts[txid]

        self.calc_score()

    def calc_score(self):
        scores = sum(self.block_predicts.values(),
                     BlockPredict(self.feerates))
        self.scores = scores
