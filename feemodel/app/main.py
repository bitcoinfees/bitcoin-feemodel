import os
import logging
import logging.handlers
import signal
from base64 import b64encode

from flask import Flask, jsonify, make_response, request, abort
from werkzeug.exceptions import default_exceptions, HTTPException

from feemodel.config import config, pkgname, __version__, datadir
from feemodel.util import pickle
from feemodel.app import SimOnline
from feemodel.txmempool import TxMempool

LOG_LEVELS = {
    levelname: getattr(logging, levelname)
    for levelname in ('DEBUG', 'INFO', 'WARNING', 'ERROR')
}
logger = logging.getLogger('feemodel')
logfile = os.path.join(datadir, 'feemodel.log')


def sigterm_handler(_signo, _stack_frame):
    raise SystemExit


def main(mempool_only=False, port=config.getint("app", "port")):
    configure_logger()
    signal.signal(signal.SIGTERM, sigterm_handler)

    app = make_json_app(__name__)
    if mempool_only:
        sim = TxMempool()
    else:
        sim = SimOnline()

    @app.route('/feemodel/mempool', methods=['GET'])
    def mempool():
        stats = sim.get_stats()
        if stats is None:
            abort(503)
        return jsonify(sim.get_stats())

    @app.route('/feemodel/transient', methods=['GET'])
    def transient():
        try:
            stats = sim.get_transientstats()
        except AttributeError:
            abort(501)
        return jsonify(stats)

    @app.route('/feemodel/pools', methods=['GET'])
    def pools():
        try:
            stats = sim.get_poolstats()
        except AttributeError:
            abort(501)
        return jsonify(stats)

    @app.route('/feemodel/poolsobj', methods=['GET'])
    def poolsobj():
        """Get the pickled representation of PoolsEstimator"""
        try:
            poolsestimate = sim.poolsonline.get_pools()
        except AttributeError:
            abort(501)
        poolspickle_b64 = b64encode(pickle.dumps(poolsestimate, protocol=2))
        obj = {"poolspickle_b64": poolspickle_b64}
        return jsonify(obj)

    @app.route('/feemodel/prediction', methods=['GET'])
    def prediction():
        try:
            stats = sim.get_predictstats()
        except AttributeError:
            abort(501)
        return jsonify(stats)

    @app.route('/feemodel/txrate', methods=['GET'])
    def txrate():
        try:
            stats = sim.get_txstats()
        except AttributeError:
            abort(501)
        return jsonify(stats)

    @app.route('/feemodel/estimatefee/<int:waitminutes>', methods=['GET'])
    def estimatefee(waitminutes):
        try:
            stats = sim.transient.stats
        except AttributeError:
            abort(501)
        if stats is None:
            abort(503)
        feerate = stats.estimatefee(waitminutes)
        if feerate is None:
            feerate = -1
        response = {'feerate': feerate, 'avgwait': waitminutes}
        return jsonify(response)

    @app.route('/feemodel/loglevel', methods=['GET', 'PUT'])
    def loglevel():
        if request.method == 'PUT':
            try:
                data = request.get_json(force=True)
                levelname = data['level'].upper()
                loglevel = LOG_LEVELS[levelname]
            except Exception:
                response = {'message': '400: bad log level.'}
                return make_response(jsonify(response), 400)
            else:
                logger.setLevel(loglevel)
        response = {"level": logging.getLevelName(logger.level)}
        return jsonify(response)

    with sim.context_start():
        # app.run(port=port, debug=True, use_reloader=False)
        logger.info("{} {} APP START".format(pkgname, __version__))
        app.run(port=port)


def configure_logger():
    formatter = logging.Formatter(
        '%(asctime)s:%(name)s [%(levelname)s] %(message)s')
    filehandler = logging.handlers.RotatingFileHandler(
        logfile, maxBytes=1000000, backupCount=1)
    filehandler.setFormatter(formatter)
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.addHandler(filehandler)

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.ERROR)


def make_json_app(import_name, **kwargs):

    def make_json_error(ex):
        response = jsonify(message=str(ex))
        response.status_code = (
            ex.code if isinstance(ex, HTTPException) else 500)
        return response

    app = Flask(import_name, **kwargs)

    for code in default_exceptions.iterkeys():
        app.error_handler_spec[None][code] = make_json_error

    return app


if __name__ == '__main__':
    main()
