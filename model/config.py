import os, json

configPath = os.path.join(os.path.dirname(__file__), '../config.json')

try:
    with open(configPath, 'r') as configFile:
        config = json.load(configFile) 
except IOError:
    raise IOError("No config.json found.")


