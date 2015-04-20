import os
import json
import ConfigParser as configparser
from pkg_resources import resource_stream, get_distribution
from feemodel.appdirs import user_data_dir

pkgname = 'bitcoin-feemodel'
__version__ = get_distribution(pkgname).version

knownpools = json.load(resource_stream(__name__, 'knownpools/pools.json'))
defaultconfigfile = resource_stream(__name__, 'default.cfg')
defaultconfig = configparser.ConfigParser()
defaultconfig.readfp(defaultconfigfile)

datadir = user_data_dir(pkgname)
if not os.path.exists(datadir):
    try:
        os.makedirs(datadir)
    except Exception as e:
        print("Error: unable to create data directory %s." % datadir)
        raise e

configfilename = os.path.join(datadir, 'feemodel.cfg')

# If config file doesn't exist, create it with default values
if not os.path.exists(configfilename):
    try:
        with open(configfilename, 'wb') as configfile:
            defaultconfig.write(configfile)
    except Exception as e:
        print("Error: unable to write to data directory %s." % datadir)
        raise e

config = configparser.ConfigParser()

try:
    config.read(configfilename)
except Exception:
    print("Warning: unable to read config file %s." % configfilename)


def load_config(section, option, opt_type=''):
    '''Return <option> from <section>.

    opt_type is "int" or "float", or the empty string (for strings).
    Tries to load from main config feemodel.cfg, if it fails, then load from
    default.cfg.
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


txmempool_config = {
    'poll_period': load_config('txmempool', 'poll_period', opt_type='int'),
    'blocks_to_keep': load_config('txmempool', 'blocks_to_keep',
                                  opt_type='int')
}

app_port = load_config('app', 'port', opt_type='int')
pools_config = {
    'window': load_config('app', 'pools_window', opt_type='int'),
    'update_period': load_config('app', 'pools_update_period', opt_type='int'),
    'minblocks': load_config('app', 'pools_minblocks', opt_type='int')
}
trans_config = {
    'update_period': load_config('app', 'trans_update_period', opt_type='int'),
    'miniters': load_config('app', 'trans_miniters', opt_type='int'),
    'maxiters': load_config('app', 'trans_maxiters', opt_type='int')
}
txrate_halflife = load_config('app', 'txrate_halflife', opt_type='int')
predict_config = {
    'block_halflife': load_config('app', 'predict_block_halflife',
                                  opt_type='int'),
    'blocks_to_keep': load_config('app', 'predict_blocks_to_keep',
                                  opt_type='int')
}

DIFF_RETARGET_INTERVAL = 2016
PRIORITYTHRESH = 57600000
MINRELAYTXFEE = 1000
