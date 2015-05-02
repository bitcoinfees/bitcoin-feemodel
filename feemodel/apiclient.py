import json
import requests
from feemodel.config import config


class APIClient(object):
    '''Client for accessing model stats through the API.'''

    def __init__(self, host='localhost', port=config.getint("app", "port")):
        self.host = host
        self.port = port

    def get_pools(self):
        return self._get_resource("pools")

    def get_poolsobj(self):
        from base64 import b64decode
        from feemodel.util import pickle
        poolspickle_b64 = self._get_resource("poolsobj")["poolspickle_b64"]
        return pickle.loads(b64decode(poolspickle_b64))

    def get_transient(self):
        return self._get_resource("transient")

    def get_mempool(self):
        return self._get_resource("mempool")

    def get_prediction(self):
        return self._get_resource("prediction")

    def get_txrate(self):
        return self._get_resource("txrate")

    def estimatefee(self, conftime):
        return self._get_resource("estimatefee/" + str(int(conftime)))

    def decidefee(self, txsize, ten_minute_cost, waitcostfn="quadratic"):
        data = {
            "txsize": txsize,
            "tenmincost": ten_minute_cost,
            "waitcostfn": waitcostfn
        }
        return self._get_resource("decidefee", data=data)

    def get_loglevel(self):
        return self._get_resource("loglevel")["level"]

    def set_loglevel(self, level):
        data = {"level": level}
        return self._put_resource('loglevel', data)["level"]

    @property
    def url(self):
        return 'http://{}:{}/feemodel/'.format(self.host, str(self.port))

    def _put_resource(self, path, data):
        headers = {"Content-type:": "application/json"}
        res = requests.put(
            self.url + path, data=json.dumps(data), headers=headers)
        res.raise_for_status()
        return res.json()

    def _get_resource(self, path, data=None):
        if data is not None:
            data = json.dumps(data)
        res = requests.get(self.url + path, data=data)
        res.raise_for_status()
        return res.json()


client = APIClient()
