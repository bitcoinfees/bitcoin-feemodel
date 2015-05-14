import os
import shutil
import json
import cPickle as pickle
from contextlib import contextmanager

import feemodel.config


@contextmanager
def tmpdatadir_context():
    tmpdatadir = mk_tmpdatadir()
    yield tmpdatadir
    rm_tmpdatadir()


def load_obj(filename):
    '''Convenience function to unpickle an object from disk.'''
    with open(filename, 'rb') as f:
        obj = pickle.load(f)
    return obj


def mk_tmpdatadir():
    README = ("This is a temporary directory for feemodel unit tests,\n"
              "and is meant to be removed after every test. Feel free\n"
              "to delete it if it failed to be removed.\n")
    if not os.path.exists(tmpdatadir):
        os.makedirs(tmpdatadir)
    with open(os.path.join(tmpdatadir, 'README'), 'w') as f:
        f.write(README)
    from feemodel.txmempool import MEMBLOCK_DBFILE
    shutil.copyfile(test_memblock_dbfile, MEMBLOCK_DBFILE)
    return tmpdatadir


def rm_tmpdatadir():
    if os.path.exists(tmpdatadir):
        assert tmpdatadir.endswith("_tmp_datadir")
        shutil.rmtree(tmpdatadir)


tmpdatadir = os.path.join(feemodel.config.datadir, '_tmp_datadir')
feemodel.config.datadir = tmpdatadir
feemodel.config.config.set("app", "port", "8351")
feemodel.config.config.set("client", "port", "8351")

# TODO: use pkg_resources for this
testdatadir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data/')
test_memblock_dbfile = os.path.join(testdatadir, 'test.db')
blockdata = os.path.join(testdatadir, 'blockdata.pickle')
with open(os.path.join(testdatadir, 'pooltags.json'), 'r') as f:
    testpooltags = json.load(f)
feemodel.config.pooltags = testpooltags

poolsref = load_obj(os.path.join(testdatadir, "poolsref.pickle"))
txref = load_obj(os.path.join(testdatadir, "txref.pickle"))
transientwaitsref = load_obj(
    os.path.join(testdatadir, "transientwaits_ref.pickle"))
transientstatsref = load_obj(
    os.path.join(testdatadir, "transientstats_ref.pickle"))

rm_tmpdatadir()
