from __future__ import division

from feemodel.util import Table


class QueueStats(object):
    def __init__(self, feerates):
        self.stats = [QueueClass(feerate) for feerate in feerates]

    def next_block(self, blockheight, blockinterval, stranding_feerate):
        if not blockinterval:
            raise ValueError("blockinterval must be > 0.")
        for queueclass in self.stats:
            queueclass.next_block(blockheight, blockinterval,
                                  stranding_feerate)

    def print_stats(self):
        table = Table()
        table.add_row(('Feerate', 'Avgwait', 'SP', 'ASB'))
        for qc in self.stats:
            table.add_row((
                qc.feerate,
                '%.2f' % qc.avgwait,
                '%.2f' % qc.stranded_proportion,
                '%.2f' % qc.avg_strandedblocks,
            ))
        table.print_table()


class QueueClass(object):
    def __init__(self, feerate):
        self.feerate = feerate
        self.totaltime = 0.
        self.totalblocks = 0
        self.total_stranded_periods = 0
        self.avgwait = 0.
        self.stranded_proportion = 0.
        self.avg_strandedblocks = 0.
        self.prevheight = None
        self.strandedblocks = []

    def next_block(self, height, blockinterval, stranding_feerate):
        if self.prevheight and height != self.prevheight + 1:
            self.strandedblocks = []

        self.prevheight = height

        stranded = self.feerate < stranding_feerate
        self.update_stranded_proportion(stranded)

        if not stranded:
            cumwait = self.update_avgwait(blockinterval, 0)
            num_stranded = len(self.strandedblocks)
            if num_stranded:
                for strandblockinterval in reversed(self.strandedblocks):
                    cumwait = self.update_avgwait(strandblockinterval, cumwait)
                self.avg_strandedblocks = (
                    self.avg_strandedblocks*self.total_stranded_periods
                    + num_stranded) / (self.total_stranded_periods+1)
                self.total_stranded_periods += 1
                self.strandedblocks = []
        else:
            self.strandedblocks.append(blockinterval)

    def update_avgwait(self, thisinterval, cumwait):
        self.avgwait = (
            self.avgwait*self.totaltime +
            thisinterval*(thisinterval*0.5 + cumwait)) / (
            self.totaltime + thisinterval)
        self.totaltime += thisinterval
        return cumwait + thisinterval

    def update_stranded_proportion(self, stranded):
        self.stranded_proportion = (
            self.stranded_proportion*self.totalblocks
            + int(stranded)) / (self.totalblocks+1)
        self.totalblocks += 1

    def __repr__(self):
        repr_str = ("QueueClass{feerate: %d, avgwait: %.2f, "
                    "stranded_proportion: %.3f, avg_strandedblocks: %.2f}" %
                    (self.feerate, self.avgwait, self.stranded_proportion,
                     self.avg_strandedblocks))
        return repr_str

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __nonzero__(self):
        return bool(self.totaltime)
