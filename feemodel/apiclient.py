import json
import requests
from feemodel.config import app_port


class APIClient(object):
    '''Client for accessing model stats through the API.'''

    def __init__(self, host='localhost', port=app_port):
        self.url = 'http://{}:{}/feemodel/'.format(host, str(port))

    def get_pools(self):
        return self._get_resource("pools")

    def get_transient(self):
        return self._get_resource("transient")

    def get_mempool(self):
        return self._get_resource("mempool")

    def get_loglevel(self):
        return self._get_resource("loglevel")["level"]

    def set_loglevel(self, level):
        data = {"level": level}
        return self._put_resource('loglevel', data)["level"]

    # def get_status(self):
    #     return self._get_resource('status')

    # def get_steadystate(self):
    #     return self._get_resource("steadystate")

    # def get_predictscores(self):
    #     return self._get_resource("predictscores")

    # def estimatefee(self, conftime):
    #     return self._get_resource("estimatefee/" + str(int(conftime)))

    def _put_resource(self, path, data):
        headers = {"Content-type:": "application/json"}
        res = requests.put(
            self.url + path, data=json.dumps(data), headers=headers)
        res.raise_for_status()
        return res.json()

    def _get_resource(self, path):
        res = requests.get(self.url + path)
        res.raise_for_status()
        return res.json()
