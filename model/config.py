import os, json

configPath = os.path.join(os.path.dirname(__file__), '../config.json')

try:
    with open(configPath, 'r') as configFile:
        config = json.load(configFile) 
except IOError:
    raise IOError("No config.json found.")

datadir = os.path.normpath(config['collectdata']['datadir'])
dbFile = os.path.join(datadir, config['collectdata']['dbname'] + 
    config['collectdata']['statVersion'])


