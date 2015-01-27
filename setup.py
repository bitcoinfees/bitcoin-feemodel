import os
import sys
import shutil
import json
from setuptools import setup, find_packages
from appdirs import user_data_dir

appname = 'bitcoin-feemodel'
appversion = '0.1.0'
datadir = user_data_dir(appname)
with open('feemodel/installinfo.py','w') as f:
    f.write('appname=\''+appname+'\'\nappversion=\''+appversion+'\'\ndatadir=\''+datadir+'\'\n')

dirwarn = False
if not os.path.exists(datadir):
    try:
        os.makedirs(datadir)
    except OSError:
        sys.exit("Error: Unable to create data directory " + datadir)
else:
    dirwarn = True

# We must run git submodule init and update
shutil.copyfile('knownpools/pools.json', os.path.join(datadir, 'pools.json'))
shutil.copyfile('./config.ini', os.path.join(datadir, 'config.ini'))

# We must require plotly and also specify plotly account
setup(
    name=appname,
    version=appversion,
    packages=find_packages(),
    scripts=['feemodel-cli', 'feemodel-txmempool'],
    description='Bitcoin transaction fee modeling and estimation package',
    author='Ian Chen',
    author_email='bitcoinfees@gmail.com',
    license='MIT',
    url='https://bitcoinfees.github.com/',
    install_requires=[
        'python-bitcoinlib',
        'Flask'
    ]
)

if dirwarn:
    print("Warning: the data directory " + datadir + " already exists.")









