from setuptools import setup, find_packages


name = 'bitcoin-feemodel'
version = '0.0.2'

setup(
    name=name,
    version=version,
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
        'feemodel': ['knownpools/pools.json', 'defaultconfig.ini']
    }
)
