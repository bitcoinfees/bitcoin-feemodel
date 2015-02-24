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


prioritythresh = load_config('general', 'prioritythresh', opt_type='int')
minrelaytxfee = load_config('general', 'minrelaytxfee', opt_type='int')

history_file = os.path.join(datadir, load_config('txmempool', 'history_file'))
poll_period = load_config('txmempool', 'poll_period', opt_type='int')
keep_history = load_config('txmempool', 'keep_history', opt_type='int')

windowfillthresh = load_config('app', 'windowfillthresh', opt_type='float')
applogfile = os.path.join(datadir, load_config('app', 'applogfile'))
loglevel = getattr(logging, load_config('app', 'loglevel').upper())
app_port = load_config('app', 'port', opt_type='int')

pools_config = {
    'window': load_config('app', 'pools_window', opt_type='int'),
    'update_period': load_config('app', 'pools_update_period', opt_type='int')
}

ss_config = {
    'window': load_config('app', 'ss_window', opt_type='int'),
    'update_period': load_config('app', 'ss_update_period', opt_type='int'),
    'maxiters': load_config('app', 'ss_maxiters', opt_type='int'),
    'miniters': load_config('app', 'ss_miniters', opt_type='int'),
    'maxtime': load_config('app', 'ss_maxtime', opt_type='int')
}

trans_config = {
    'window': load_config('app', 'trans_window', opt_type='int'),
    'update_period': load_config('app', 'trans_update_period', opt_type='int'),
    'maxiters': load_config('app', 'trans_maxiters', opt_type='int'),
    'miniters': load_config('app', 'trans_miniters', opt_type='int'),
    'maxtime': load_config('app', 'trans_maxtime', opt_type='int')
}
