import os
from feemodel.util import load_obj

# TODO: use pkg_resources for this
datadir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data/')
memblock_dbfile = os.path.join(datadir, 'test.db')
blockdata = os.path.join(datadir, 'blockdata.pickle')

poolsref = load_obj(os.path.join(datadir, "pe_ref.pickle"))
txref = load_obj(os.path.join(datadir, "tr_ref.pickle"))
transientwaitsref = load_obj(os.path.join(datadir, "transientwaits_ref.pickle"))
transientstatsref = load_obj(os.path.join(datadir, "transientstats_ref.pickle"))
