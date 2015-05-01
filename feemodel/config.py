import os
import json
import ConfigParser
from pkg_resources import resource_stream, get_distribution
from feemodel.appdirs import user_data_dir

pkgname = 'bitcoin-feemodel'
__version__ = get_distribution(pkgname).version

pooltags = json.load(resource_stream(__name__, 'pooltags.json'))

DIFF_RETARGET_INTERVAL = 2016
PRIORITYTHRESH = 57600000
EXPECTED_BLOCK_INTERVAL = 600
MINRELAYTXFEE = 1000

# Create the default data directory.
datadir = user_data_dir(pkgname)
if not os.path.exists(datadir):
    try:
        os.makedirs(datadir)
    except Exception as e:
        print("Error: unable to create data directory %s." % datadir)
        raise e

config = ConfigParser.ConfigParser()
defaultconfigfile = resource_stream(__name__, 'default.cfg')
configfilename = os.path.join(datadir, 'feemodel.cfg')
config.readfp(defaultconfigfile)
config.read(configfilename)
