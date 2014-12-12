import os
try:
    from installinfo import datadir
except ImportError:
    sys.exit("Error: Package has not been installed.")

config = {
    "pollPeriod": 5,
    "keepHistory": 2016, # Making this smaller will erase part of the history.
    "historyDb": "history.db",
    "nonparam": {
        "numBlocksUsed": (6, 144), 
        "maxBlockAge": 432,
        "statsDb": "stats.db",
        "numBootstrap": 1000,
    },
    "logging": {
        "logFile": 'debug.log',
        "toStdOut": True
    }
}

statsFile = os.path.join(datadir, config['nonparam']['statsDb'])
historyFile = os.path.join(datadir, config['historyDb'])
logFile = os.path.join(datadir, 'debug.log')

apprun = False