import requests
from feemodel.config import app_port

__all__ = ['APIClient', 'InvalidCommandException', 'NotReadyException']


class APIClient(object):
    '''Client for accessing model stats through the API.'''

    def __init__(self, host='localhost', port=app_port):
        self.url = 'http://{}:{}/feemodel/'.format(host, str(port))

    def get_status(self):
        return self.get_resource('status')

    def get_steadystate(self):
        return self.get_resource("steadystate")

    def get_transient(self):
        return self.get_resource("transient")

    def get_predictscores(self):
        return self.get_resource("predictscores")

    def get_pools(self):
        return self.get_resource("pools")

    def estimatefee(self, conftime):
        return self.get_resource("estimatefee/" + str(int(conftime)))

    def get_resource(self, path):
        r = requests.get(self.url + path)
        stat = r.json()
        if not r:
            raise InvalidCommandException
        elif not stat:
            raise NotReadyException
        return stat


class InvalidCommandException(Exception):
    '''Invalid API resource.'''
    pass


class NotReadyException(Exception):
    '''App not ready; no stats available.'''
    pass
