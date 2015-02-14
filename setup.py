import os
import sys
import shutil
from setuptools import setup, find_packages
from appdirs import user_data_dir

appname = 'bitcoin-feemodel'
appversion = '0.1.0'
datadir = user_data_dir(appname)

with open('feemodel/installinfo.py', 'w') as f:
    f.write(
        'appname = \'{}\'\n'
        'appversion = \'{}\'\n'
        'datadir = \'{}\'\n'.format(appname, appversion, datadir))

direxists = False
if not os.path.exists(datadir):
    try:
        os.makedirs(datadir)
    except OSError:
        sys.exit("Error: Unable to create data directory " + datadir)
else:
    direxists = True

try:
    setup(
        name=appname,
        version=appversion,
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
        entry_points='''
            [console_scripts]
            feemodel-cli = feemodel.cli:cli
        '''
    )

    # We must run git submodule init and update
    shutil.copyfile('knownpools/pools.json',
                    os.path.join(datadir, 'pools.json'))
    shutil.copyfile('./config.ini', os.path.join(datadir, 'config.ini'))
except Exception as e:
    if not direxists:
        os.rmdir(datadir)
    raise(e)
else:
    if direxists:
        print("Warning: the data directory " + datadir + " already exists.")
