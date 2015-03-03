import logging
import signal
from flask import Flask, jsonify, make_response
from feemodel.config import (applogfile, loglevel, app_port,
                             pkgname, __version__)
from feemodel.app import SimOnline
from feemodel.txmempool import TxMempool


def sigterm_handler(_signo, _stack_frame):
    raise SystemExit


def main(mempool_only=False, port=app_port):
    formatter = logging.Formatter(
        '%(asctime)s:%(name)s [%(levelname)s] %(message)s')
    filehandler = logging.FileHandler(applogfile)
    filehandler.setLevel(loglevel)
    filehandler.setFormatter(formatter)
    logger = logging.getLogger('feemodel')
    logger.setLevel(loglevel)
    logger.addHandler(filehandler)

    signal.signal(signal.SIGTERM, sigterm_handler)
    app = Flask(__name__)
    if mempool_only:
        sim = TxMempool()
    else:
        sim = SimOnline()

    @app.route('/feemodel/status', methods=['GET'])
    def get_status():
        stats = sim.get_status()
        stats = stats if stats else {}
        return jsonify(stats)

    if not mempool_only:
        @app.route('/feemodel/pools', methods=['GET'])
        def get_pools():
            stats = sim.peo.pe.get_stats()
            stats = stats if stats else {}
            return jsonify(stats)

        @app.route('/feemodel/steadystate', methods=['GET'])
        def get_steadystate():
            stats = sim.ss.stats.get_stats()
            stats = stats if stats else {}
            return jsonify(stats)

        @app.route('/feemodel/transient', methods=['GET'])
        def get_transient():
            stats = sim.trans.stats.get_stats()
            stats = stats if stats else {}
            return jsonify(stats)

        @app.route('/feemodel/predictscores', methods=['GET'])
        def get_predicts():
            stats = sim.prediction.get_stats()
            stats = stats if stats else {}
            return jsonify(stats)

        @app.route('/feemodel/estimatefee/<int:waitminutes>', methods=['GET'])
        def estimatefee(waitminutes):
            stats = sim.trans.stats
            if not stats:
                response = {}
            else:
                feerate = sim.trans.stats.avgwaits.inv(waitminutes*60)
                if feerate is None:
                    feerate = -1
                response = {'feerate': int(feerate), 'avgwait': waitminutes}
            return jsonify(response)

    @app.errorhandler(404)
    def not_found(err):
        return make_response(jsonify({'error': 'Not found'}), 404)

    with sim.context_start():
        # app.run(port=port, debug=True, use_reloader=False)
        logger.info("{} {} APP START".format(pkgname, __version__))
        app.run(port=port)


if __name__ == '__main__':
    main()
