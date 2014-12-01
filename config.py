import os

here = os.path.abspath(os.path.dirname(__file__))

config = {
    "priorityThresh": 57e6,
    "datadir": os.path.join(here, 'data/'),
    "dbname": 'txdata',
    "logFile": os.path.join(here, 'debug.log')
    "collectdata": {
        "pollPeriod": 3,
        "leadTimeMargin": 5,
        "defaultLeadTime": 60,
    },
    "model": {
        "bootstrapSamples": 1000
    }
}

dbFile = os.path.join(config['datadir'], config['dbname'])
