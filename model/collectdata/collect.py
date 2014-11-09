import os, json
import cPickle as pickle
from time import sleep, time
from txmempool import TxMempool, TxMempoolEntry

# datadir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data/')
try:
    configPath = os.path.join(os.path.dirname(__file__), '../../config.json')
except IOError:
    raise IOError("No config.json found.")

with open(configPath, 'r') as configFile:
    config = json.load(configFile) 

datadir = os.path.normpath(config['collectdata']['datadir'])
pollperiod =  config['collectdata']['pollperiod']

if not os.path.exists(datadir):
    os.mkdir(datadir)


def collect():

    mempool = TxMemPool()


if __name__ == '__main__':
    pass