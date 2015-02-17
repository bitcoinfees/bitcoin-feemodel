import logging
import os
import ConfigParser as configparser
try:
    from installinfo import datadir
except ImportError as e:
    print("Package has not been installed.")
    raise(e)

config = configparser.ConfigParser()
config_file = os.path.join(datadir, 'config.ini')
config.read(config_file)

defaults = {
    'general': {
        # Transaction must have priority strictly greater than this to be
        # considered 'high priority' by Bitcoin Core.
        'prioritythresh': 57600000,
    },
    'txmempool': {
        'poll_period': 5,
        'keep_history': 2400,
        'history_file': 'history.db',
    },
    'app': {
        # ratio of (number of memblocks in window) to (window length)
        # must be at least this number.
        'windowfillthresh': 0.9,
        'applog': 'debug.log',
        'loglevel': 'DEBUG',
        'port': 8350
    }
}


def write_default_config(filename):
    with open(filename, 'w') as f:
        for section, opts in defaults.items():
            f.write('[{}]\n'.format(section))
            for opt, val in opts.items():
                f.write('{} = {}\n'.format(opt, str(val)))
            f.write('\n')


def load_config(section, option, opt_type=''):
    '''Return <option> from <section>.
    opt_type is "int" or "float", or the empty string (for strings).
    '''
    getfn = getattr(config, 'get' + opt_type)
    try:
        return getfn(section, option)
    except Exception:
        defaultval = defaults[section][option]
        print("Unable to load %s/%s config; using default value of %s" %
              (option, section, str(defaultval)))
        return defaultval


poll_period = load_config('txmempool', 'poll_period', opt_type='int')
keep_history = load_config('txmempool', 'keep_history', opt_type='int')
prioritythresh = load_config('general', 'prioritythresh', opt_type='int')
windowfillthresh = load_config('app', 'windowfillthresh', opt_type='float')
history_file = os.path.join(datadir, load_config('txmempool', 'history_file'))
applogfile = os.path.join(datadir, load_config('app', 'applog'))
loglevel = getattr(logging, load_config('app', 'loglevel').upper())
app_port = load_config('app', 'port', opt_type='int')

poolinfo_file = os.path.join(datadir, 'pools.json')

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
