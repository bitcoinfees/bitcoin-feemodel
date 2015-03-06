from setuptools import setup, find_packages

name = 'bitcoin-feemodel'
version = '0.0.4'

with open('README', 'r') as f:
    readme = f.read()

setup(
    name=name,
    version=version,
    packages=find_packages(),
    description='Bitcoin transaction fee modeling/simulation/estimation',
    long_description=readme,
    author='Ian Chen',
    author_email='bitcoinfees@gmail.com',
    license='MIT',
    url='https://github.com/bitcoinfees/bitcoin-feemodel',
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
        'feemodel': ['knownpools/pools.json', 'default.cfg']
    }
)
