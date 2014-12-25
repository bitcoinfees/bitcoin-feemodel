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
    "queue": {
        "saveQueue": "saveQueue.pickle",
        "feeResolution": 1000,
        "adaptiveWindow": 2016
    },
    "measurement": {
        "saveWait": "saveWait.pickle",
        "priorityThresh": 57.6e6
    },
    "simul": {
        "savePoolBlocks": "savePoolBlocks.pickle",
        "savePools": "savePools.db"
    },
    "logging": {
        "logFile": 'debug.log',
        "toStdOut": True
    }
}

statsFile = os.path.join(datadir, config['nonparam']['statsDb'])
saveQueueFile = os.path.join(datadir, config['queue']['saveQueue'])
saveWaitFile = os.path.join(datadir, config['measurement']['saveWait'])
savePoolBlocksFile = os.path.join(datadir, config['simul']['savePoolBlocks'])
savePoolsFile = os.path.join(datadir, config['simul']['savePools'])
historyFile = os.path.join(datadir, config['historyDb'])
logFile = os.path.join(datadir, 'debug.log')

poolInfoFile = os.path.join(datadir, 'pools.json')

apprun = False # If true, TxMempool.processBlocks will write history, and logWrite will write to the log file