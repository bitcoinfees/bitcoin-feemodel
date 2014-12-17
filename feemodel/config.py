import os
try:
    from installinfo import datadir
except ImportError:
    sys.exit("Error: Package has not been installed.")

config = {
    "pollPeriod": 5,
    "keepHistory": 2016, # Making this smaller will erase part of the history.
    "historyDb": "history.db",
    "predictionFeeResolution": 10000,
    "predictionMaxBlocks": 25,
    "nonparam": {
        "numBlocksUsed": (6, 144), # ends-inclusive
        "maxBlockAge": 432,
        "sigLevel": 0.9,
        "statsDb": "stats.db",
        "numBootstrap": 1000,
        "minP": 0.3
    },
    "logging": {
        "logFile": 'debug.log',
        "toStdOut": True
    }
}

statsFile = os.path.join(datadir, config['nonparam']['statsDb'])
historyFile = os.path.join(datadir, config['historyDb'])
logFile = os.path.join(datadir, 'debug.log')

apprun = False # If true, TxMempool.processBlocks will write history, and logWrite will write to the log file