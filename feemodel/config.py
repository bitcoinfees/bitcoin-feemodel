import logging
import os
import json
import ConfigParser as configparser
from pkg_resources import resource_stream, resource_string
from feemodel.appdirs import user_data_dir


knownpools = json.load(resource_stream(__name__, 'knownpools/pools.json'))
defaultconfigfile = resource_stream(__name__, 'defaultconfig.ini')
defaultconfig = configparser.ConfigParser()
defaultconfig.readfp(defaultconfigfile)

datadir = user_data_dir('bitcoin-feemodel')
if not os.path.exists(datadir):
    try:
        os.makedirs(datadir)
    except Exception as e:
        print("Error: unable to create data directory %s." % datadir)
        raise e

configfilename = os.path.join(datadir, 'config.ini')
if not os.path.exists(configfilename):
    defaultconfigstr = resource_string(__name__, 'defaultconfig.ini')
    try:
        with open(configfilename, 'w') as f:
            f.write(defaultconfigstr)
    except Exception as e:
        print("Error: unable to write to data directory %s." % datadir)

config = configparser.ConfigParser()
try:
    config.read(configfilename)
except Exception:
    print("Error: unable to read config file %s." % configfilename)


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
