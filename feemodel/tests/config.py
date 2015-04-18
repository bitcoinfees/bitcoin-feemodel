import os
import shutil
import cPickle as pickle
from contextlib import contextmanager

import feemodel.config
from feemodel.config import datadir


@contextmanager
def setup_tmpdatadir():
    # 1. Create the tmp data dir
    # 2. Copy the test memblock db there
    # 3. yield the tmp data dir path
    # 4. Remove the whole dir afterward
    README = ("This is a temporary directory for feemodel unit tests,\n"
              "and is meant to be removed after every test. Feel free\n"
              "to delete it if it failed to be removed.\n")
    if not os.path.exists(tmpdatadir):
        os.makedirs(tmpdatadir)
    with open(os.path.join(tmpdatadir, 'README'), 'w') as f:
        f.write(README)
    from feemodel.txmempool import MEMBLOCK_DBFILE
    shutil.copyfile(test_memblock_dbfile, MEMBLOCK_DBFILE)
    yield tmpdatadir
    rm_tmpdatadir()


def load_obj(filename):
    '''Convenience function to unpickle an object from disk.'''
    with open(filename, 'rb') as f:
        obj = pickle.load(f)
    return obj


def rm_tmpdatadir():
    if os.path.exists(tmpdatadir):
        assert tmpdatadir.endswith("_tmp")
        shutil.rmtree(tmpdatadir)


tmpdatadir = os.path.join(datadir, '_tmp')
feemodel.config.datadir = tmpdatadir

# TODO: use pkg_resources for this
testdatadir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data/')
test_memblock_dbfile = os.path.join(testdatadir, 'test.db')
blockdata = os.path.join(testdatadir, 'blockdata.pickle')

poolsref = load_obj(os.path.join(testdatadir, "pe_ref.pickle"))
txref = load_obj(os.path.join(testdatadir, "tr_ref.pickle"))
transientwaitsref = load_obj(
    os.path.join(testdatadir, "transientwaits_ref.pickle"))
transientstatsref = load_obj(
    os.path.join(testdatadir, "transientstats_ref.pickle"))

rm_tmpdatadir()
