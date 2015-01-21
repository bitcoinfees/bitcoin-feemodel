import feemodel.simul
import feemodel.plotting
from flask import Flask
import json
from functools import wraps
import sys

def addPreTag(fn):
    @wraps(fn)
    def decorated():
        s = fn()
        return '<pre>' + s + '</pre>'
    return decorated


def main(port=5001):
    s = feemodel.simul.SimulOnline()
    app = Flask(__name__)

    @app.route('/waittimes')
    def getWaitTimes():
        return json.dumps(s.getWaitTimes())

    @app.route('/steadystats')
    def getSteadyStats():
        return json.dumps(s.getSteadyStats())

    @app.route('/pools')
    def getPools():
        return json.dumps(s.getPools())

    @app.route('/transientstats')
    def getTransientStats():
        return json.dumps(s.getTransientStats())

    @app.route('/predictscores')
    def getPredictScores():
        return json.dumps(s.getPredictions())

    with s.threadStart():
        app.run(port=port, debug=True, use_reloader=False)



if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        print("Entering test mode.")
        feemodel.simul.waitTimesGraph = feemodel.plotting.waitTimesGraphTest
        feemodel.simul.transWaitGraph = feemodel.plotting.transWaitGraphTest
        feemodel.simul.confTimeGraph = feemodel.plotting.confTimeGraphTest
        feemodel.simul.capsGraph = feemodel.plotting.capsGraphTest

    main()

