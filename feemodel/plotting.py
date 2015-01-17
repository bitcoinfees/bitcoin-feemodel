import time
import plotly.plotly as py
import threading
from feemodel.util import tryWrap

plotly_user = 'bitcoinfees'
waitTimesFile = (274, 'combinedwaits')
transWaitFile = (378, 'transwait')
ratesFile = (338, 'rates')

graphLock = threading.RLock()


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


waitTimesGraph = WaitTimesGraph(*waitTimesFile)
ratesGraph = RatesGraph(*ratesFile)
transWaitGraph = TransWaitGraph(*transWaitFile)
