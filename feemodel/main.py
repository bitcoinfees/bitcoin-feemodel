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

def json_pprint(s):
    return json.dumps(s, indent=4, separators=(',', ': '))

def main():
    s = SimulOnline()
    app = Flask(__name__)

    @app.route('/waittimes')
    def getWaitTimes():
        return json.dumps(s.getWaitTimes())

    @app.route('/steadystats')
    def getSteadyStats():
        return json.dumps(s.getSteadyStats())

    with s.threadStart():
        app.run(port=5001, debug=True, use_reloader=False)



if __name__ == '__main__':
    main()
    
