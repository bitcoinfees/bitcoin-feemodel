import os
import json
import shutil
from setuptools import setup, find_packages
from appdirs import user_data_dir

installinfo = {
    'name': 'bitcoin-feemodel',
    'version': '0.0.1',
}
datadir = user_data_dir(installinfo['name'])
installinfo.update({'datadir': datadir})

with open('feemodel/installinfo.json', 'w') as f:
    json.dump(installinfo, f)

datadirexists = False
if not os.path.exists(datadir):
    try:
        os.makedirs(datadir)
    except Exception as e:
        print("Error: Unable to create data directory " + datadir)
        raise e
else:
    datadirexists = True

try:
    setup(
        name=installinfo['name'],
        version=installinfo['version'],
        packages=find_packages(),
        description='Bitcoin transaction fee modeling/simulation/estimation',
        author='Ian Chen',
        author_email='bitcoinfees@gmail.com',
        license='MIT',
        url='https://bitcoinfees.github.com/',
        install_requires=[
            'python-bitcoinlib',
            'Flask',
            'click',
            'requests',
            'tabulate'
        ],
        entry_points={
            'console_scripts': ['feemodel-cli = feemodel.cli:cli']
        },
        package_data={
            'feemodel': ['knownpools/pools.json',
                         'installinfo.json',
                         'defaultconfig.ini']
        }
    )

    shutil.copyfile('feemodel/defaultconfig.ini',
                    os.path.join(datadir, 'config.ini'))
except Exception as e:
    if not datadirexists:
        os.rmdir(datadir)
    raise(e)
else:
    if datadirexists:
        print("Warning: the data directory " + datadir + " already exists.")
