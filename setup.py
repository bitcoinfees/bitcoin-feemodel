from setuptools import setup, find_packages
from Cython.Build import cythonize

name = 'bitcoin-feemodel'
version = '0.1.0'

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
    ext_modules=cythonize([
        "feemodel/simul/txsources.pyx",
        "feemodel/simul/simul.pyx",
        "feemodel/stranding.pyx"
    ]),
    entry_points={
        'console_scripts': ['feemodel = feemodel.cli:cli']
    },
    package_data={
        'feemodel': ['pooltags.json', 'default.cfg']
    }
)
