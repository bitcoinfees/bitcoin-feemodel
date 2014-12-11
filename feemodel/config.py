import os

config = {
    "datadir": 'data',
    "logFile": 'debug.log',
    "pollPeriod": 5,
    "nonparam": {
        "numBlockRange": (6, 144), 
        "maxBlockAge": 432,
        "keepHistory": 2016, # How many blocks to keep history for
        "historyDb": "history",
        "statsDb": "stats",
        "numBootstrap": 1000,
    },
    "logging": {
        "logFile": 'debug.log',
        "toStdOut": True
    }
}

here = os.path.abspath(os.path.dirname(__file__))

statsFile = os.path.join(here, config['datadir'], config['nonparam']['statsDb'])
historyFile = os.path.join(here, config['datadir'], config['nonparam']['historyDb'])
logFile = os.path.join(here, config['logging']['logFile'])