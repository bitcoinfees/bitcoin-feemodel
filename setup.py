import os, sys
from setuptools import setup, find_packages
from feemodel.appdirs import user_data_dir

appname = 'bitcoin-feemodel'
appversion = '0.1.0' 
datadir = user_data_dir(appname)

setup(
    name=appname,
    version=appversion,
    packages=find_packages(),
    scripts=['feemodel-run'],
    description='Bitcoin transaction fee modeling and estimation package',
    author='Ian Chen',
    author_email='bitcoinfees@gmail.com',
    license='MIT',
    url='https://bitcoinfees.github.com/',
    install_requires=[
        'python-bitcoinlib'
    ]
)

if not os.path.exists(datadir):
    try:
        os.mkdir(datadir)
    except OSError:
        sys.exit("Error: Unable to create data directory " + datadir)
else:
    print("Warning: the data directory " + datadir + " already exists.")






