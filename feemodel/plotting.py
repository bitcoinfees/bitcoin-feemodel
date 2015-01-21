import time
import plotly.plotly as py
import plotly.tools as tls
from plotly.grid_objs import *
import threading
from feemodel.util import tryWrap
from datetime import datetime

plotly_user = tls.get_credentials_file()['username']

poolsGridFile = 'poolsgrid'

waitTimesFile = (274, 'combinedwaits')
transWaitFile = (378, 'transwait')
#ratesFile = (338, 'rates')
capFile = (499, 'caps')
confTimeFile = (517, 'conftimeseries')

test_waitTimesFile = (479, 'combinedwaits (test)')
test_transWaitFile = (475, 'transwait (test)')
test_confTimeFile = (527, 'conftimeseries (test)')
test_capFile = (516, 'caps (test)')
#test_ratesFile = (338, 'rates')

graphLock = threading.RLock()

class PlotlyGrid(object):
    def __init__(self, grid_filename):
        self.grid_filename = grid_filename
        self.cols = []

    def appendColumn(self, colname, colvals):
        self.cols.append(Column(colvals, colname))

    def postGrid(self):
        with graphLock:
            grid = Grid(self.cols)
            py.grid_ops.upload(grid, self.grid_filename, auto_open=False)


class PoolsGrid(PlotlyGrid):
    def __init__(self):
        super(self.__class__, self).__init__(poolsGridFile)

    def plotBubbleChart(self, bubbleFile='poolbubble'):
        grid = Grid(self.cols)
        names = Grid.get_column('name')
        proportions = Grid.get_column('proportion')
        maxBlockSizes = Grid.get_column('maxBlockSize')
        minFeeRates = Grid.get_column('minFeeRate')
        if not names or not proportions or not maxBlockSizes or not minFeeRates:
            logWrite("Error in pool data.")
            return
        pools = zip(names,proportions,maxBlockSizes,minFeeRates)




class Graph(object):
    def __init__(self, graph_id, graph_filename):
        self.graph_id = graph_id
        self.graph_filename = graph_filename
        self.fig = None

    def modifyDatetime(self):
        if not self.fig:
            raise ValueError("Fig not available - run getFig first")
        currTitle = self.fig['layout']['title']
        timeIdx = currTitle.find('(updated')
        if timeIdx == -1:
            raise ValueError("The graph title does not have proper formatting.")
        timeString = '(updated %s)' % time.strftime('%Y/%m/%d %H:%M %Z')
        self.fig['layout']['title'] = currTitle[:timeIdx] + timeString

    def getFig(self):
        with graphLock:
            self.fig = py.get_figure(plotly_user, self.graph_id)

    def postFig(self):
        with graphLock:
            py.plot(self.fig, filename=self.graph_filename, auto_open=False)

    def clearXY(self):
        with graphLock:
            self.getFig()
            for trace in self.fig['data']:
                trace.update(dict(x=[], y=[]))
            self.postFig()


class WaitTimesGraph(Graph):
    # Wrap with a retry.
    @tryWrap
    def updateSteadyState(self, x, steady_y, measured_y, m_error):
        with graphLock:
            self.getFig()
            self.fig['data'][0].update({'x': x, 'y': steady_y})

            xbin = [(x[idx]+x[idx-1])/2. for idx in range(1, len(x))]
            measured_y = measured_y[:-1]
            m_error = m_error[:-1]
            self.fig['data'][1].update({'x': xbin, 'y': measured_y, 'error_y': {'array': m_error}})
            self.modifyDatetime()
            self.postFig()

    # We probably don't want transient data in the same graph as the steady state
    @tryWrap
    def updateTransient(self, x, y, onesidedErr):
        with graphLock:
            self.getFig()
            self.fig['data'][1].update({
                'x': x,
                'y': y,
                'error_y': {'array': onesidedErr}
            })
            self.modifyDatetime()
            self.postFig()


class TransWaitGraph(Graph):
    @tryWrap
    def updateAll(self, x, y, onesidedErr):
        with graphLock:
            self.getFig()
            old_x = self.fig['data'][0]['x']
            old_y = self.fig['data'][0]['y']
            self.fig['data'][1].update(dict(
                x=old_x,
                y=old_y
            ))
            self.fig['data'][0].update(dict(
                x=x,
                y=y,
                error_y= {'array':onesidedErr}
            ))
            self.modifyDatetime()
            self.postFig()


# Deprecated
class RatesGraph(Graph):
    @tryWrap
    def updateAll(self, feeClasses, procrate, procrateUpper, txByteRate, stableStat):
        with graphLock:
            self.getFig()
            self.fig['data'][0].update({
                'x': feeClasses,
                'y': procrate
            })
            self.fig['data'][1].update({
                'x': feeClasses,
                'y': procrateUpper
            })
            self.fig['data'][2].update({
                'x': feeClasses,
                'y': txByteRate
            })
            self.fig['layout']['annotations'][0].update({
                'x': stableStat[0],
                'y':stableStat[1],
                'text': 'Stable fee rate: %d' % stableStat[0]
            })
            self.modifyDatetime()
            self.postFig()


class CapsGraph(Graph):
    @tryWrap
    def updateAll(self, x, y0, y1, y2):
        with graphLock:
            self.getFig()
            self.fig['data'][0].update(dict(x=x, y=y0))
            self.fig['data'][1].update(dict(x=x, y=y1))
            self.fig['data'][2].update(dict(x=x, y=y2))
            self.modifyDatetime()
            self.postFig()


class ConfTimeGraph(Graph):
    maxPoints = 360
    @tryWrap
    def updateAll(self, t, txByteRate, mempoolSize):
        with graphLock:
            self.getFig()

            x = self.fig['data'][0].get('x')
            if x is None:
                x = []
            x.append(datetime.now())
            x = self._keepRecent(x, self.maxPoints)

            y0 = self.fig['data'][0].get('y')
            y1 = self.fig['data'][1].get('y')
            y2 = self.fig['data'][2].get('y')
            if y0 is None:
                y0 = []
            if y1 is None:
                y1 = []
            if y2 is None:
                y2 = []
            y0.append(t)
            y1.append(txByteRate)
            y2.append(mempoolSize)

            self.fig['data'][0]['x'] = x
            self.fig['data'][1]['x'] = x
            self.fig['data'][2]['x'] = x

            self.fig['data'][0]['y'] = self._keepRecent(y0, self.maxPoints)
            self.fig['data'][1]['y'] = self._keepRecent(y1, self.maxPoints)
            self.fig['data'][2]['y'] = self._keepRecent(y2, self.maxPoints)

            self.postFig()

    @staticmethod
    def _keepRecent(l, numpoints):
        start = max(0, len(l)-numpoints)
        return l[start:]


#poolsGrid = PlotlyGrid(poolsGridFile)

waitTimesGraph = WaitTimesGraph(*waitTimesFile)
#ratesGraph = RatesGraph(*ratesFile)
transWaitGraph = TransWaitGraph(*transWaitFile)
capsGraph = CapsGraph(*capFile)
confTimeGraph = ConfTimeGraph(*confTimeFile)

waitTimesGraphTest = WaitTimesGraph(*test_waitTimesFile)
transWaitGraphTest = TransWaitGraph(*test_transWaitFile)
confTimeGraphTest = ConfTimeGraph(*test_confTimeFile)
capsGraphTest = CapsGraph(*test_capFile)
