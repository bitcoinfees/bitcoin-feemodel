import os
import json
import ConfigParser as configparser

try:
    from installinfo import datadir
except ImportError as e:
    print("Package has not been installed.")
    raise(e)

config = configparser.ConfigParser()
config_file = os.path.join(datadir, 'config.ini')
try:
    config.read(config_file)
except Exception as e:
    print("Unable to load config.ini.")
    raise(e)

# statsFile = os.path.join(datadir, config['nonparam']['statsDb'])
# saveQueueFile = os.path.join(datadir, config['queue']['saveQueue'])
# saveWaitFile = os.path.join(datadir, config['measurement']['saveWait'])
# saveRatesFile = os.path.join(datadir, config['measurement']['saveRates'])
# savePoolsFile = os.path.join(datadir, config['simul']['savePools'])
# saveSSFile = os.path.join(datadir, config['simul']['saveSS'])
# savePredictFile = os.path.join(datadir, config['simul']['savePredict'])
# historyFile = os.path.join(datadir, config['historyDb'])
# logFile = os.path.join(datadir, 'debug.log')

history_file = os.path.join(datadir, config.get('txmempool', 'history_file'))
poolinfo_file = os.path.join(datadir, 'pools.json')

