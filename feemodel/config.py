import logging
import os
import json
import ConfigParser as configparser
from pkg_resources import resource_stream

try:
    defaultconfigfile = resource_stream(__name__, 'defaultconfig.ini')
    installinfo = json.load(resource_stream(__name__, 'installinfo.json'))
    knownpools = json.load(resource_stream(__name__, 'knownpools/pools.json'))
except Exception as e:
    print("Package has not been installed.")
    raise e

datadir = installinfo['datadir']

config = configparser.ConfigParser()
config_file = os.path.join(datadir, 'config.ini')
try:
    config.read(config_file)
except Exception:
    print("Error reading config file at %s" % config_file)

defaultconfig = configparser.ConfigParser()
config.readfp(defaultconfigfile)


def load_config(section, option, opt_type=''):
    '''Return <option> from <section>.
    opt_type is "int" or "float", or the empty string (for strings).
    '''
    try:
        getfn = getattr(config, 'get' + opt_type)
        return getfn(section, option)
    except Exception:
        getfn = getattr(defaultconfig, 'get' + opt_type)
        defaultval = getfn(section, option)
        print("Unable to load %s/%s config; using default value of %s" %
              (option, section, str(defaultval)))
        return defaultval


poll_period = load_config('txmempool', 'poll_period', opt_type='int')
keep_history = load_config('txmempool', 'keep_history', opt_type='int')
prioritythresh = load_config('general', 'prioritythresh', opt_type='int')
windowfillthresh = load_config('app', 'windowfillthresh', opt_type='float')
history_file = os.path.join(datadir, load_config('txmempool', 'history_file'))
applogfile = os.path.join(datadir, load_config('app', 'applogfile'))
loglevel = getattr(logging, load_config('app', 'loglevel').upper())
app_port = load_config('app', 'port', opt_type='int')

# poolinfo_file = os.path.join(datadir, 'pools.json')
# apilogfile = os.path.join(datadir, load_config('app', 'apilog'))
# statsFile = os.path.join(datadir, config['nonparam']['statsDb'])
# saveQueueFile = os.path.join(datadir, config['queue']['saveQueue'])
# saveWaitFile = os.path.join(datadir, config['measurement']['saveWait'])
# saveRatesFile = os.path.join(datadir, config['measurement']['saveRates'])
# savePoolsFile = os.path.join(datadir, config['simul']['savePools'])
# saveSSFile = os.path.join(datadir, config['simul']['saveSS'])
# savePredictFile = os.path.join(datadir, config['simul']['savePredict'])
# historyFile = os.path.join(datadir, config['historyDb'])
# logFile = os.path.join(datadir, 'debug.log')
