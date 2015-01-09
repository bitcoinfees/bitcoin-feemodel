from feemodel.simul import SimulOnline
from flask import Flask
import json
from functools import wraps

def addPreTag(fn):
    @wraps(fn)
    def decorated():
        s = fn()
        return '<pre>' + s + '</pre>'
    return decorated


def main(port=5001):
    s = SimulOnline()
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

    with s.threadStart():
        app.run(port=port, debug=True, use_reloader=False)



if __name__ == '__main__':
    main()
    
