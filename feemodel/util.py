from __future__ import division

import threading
import logging
from bisect import insort, bisect
from math import ceil, log
from contextlib import contextmanager
from random import random
from functools import wraps
from time import time
try:
    import cPickle as pickle
except ImportError:
    import pickle

from bitcoin.rpc import Proxy, JSONRPCException
from bitcoin.wallet import CBitcoinAddress, CBitcoinAddressError
from bitcoin.core import COIN


logger = logging.getLogger(__name__)


class StoppableThread(threading.Thread):
    '''A thread with a stop flag.'''

    def __init__(self):
        super(StoppableThread, self).__init__(name=self.__class__.__name__)
        self.__stopflag = threading.Event()

    def run(self):
        '''The target function of the thread.'''
        raise NotImplementedError

    def stop(self):
        '''Set the stop flag.'''
        self.__stopflag.set()

    def is_stopped(self):
        '''Returns True if stop flag is set, else False.'''
        return self.__stopflag.is_set()

    def sleep(self, secs):
        '''Like time.sleep but terminates immediately once stop flag is set.'''
        self.__stopflag.wait(timeout=secs)

    @contextmanager
    def thread_start(self):
        '''Context manager for starting/closing the thread.

        Starts the thread and terminates it cleanly at the end of the
        context block.
        '''
        self.start()
        try:
            yield
        finally:
            self.stop()
            self.join()

    def get_stop_object(self):
        '''Returns the stop flag object.'''
        return self.__stopflag

    @staticmethod
    def auto_restart(interval):
        '''Returns an auto-restart decorator for StoppableThread methods.

        The decorator causes the target method to auto-restart, after
        interval seconds, in the event of an unhandled exception.

        The target method must belong to a StoppableThread instance.
        '''
        def decorator(fn):
            @wraps(fn)
            def looped_fn(self, *args, **kwargs):
                while not self.is_stopped():
                    try:
                        fn(self, *args, **kwargs)
                    except Exception as e:
                        logger.exception(
                            '{} in {}, restarting in {} seconds.'.format(
                                e.__class__.__name__, self.name, interval))
                        self.sleep(interval)
            return looped_fn
        return decorator


class BlockingProxy(Proxy):
    '''Thread-safe version of bitcoin.rpc.Proxy.

    In addition, if there was a connection related exception, close the
    connection before re-raising.
    '''

    def __init__(self):
        super(BlockingProxy, self).__init__()
        self.rlock = threading.RLock()

    def _call(self, *args):
        with self.rlock:
            try:
                return super(BlockingProxy, self)._call(*args)
            except Exception as e:
                self.close()
                raise e

    def close(self):
        with self.rlock:
            self._RawProxy__conn.close()


class BatchProxy(BlockingProxy):
    '''Proxy with batch calls.'''

    def poll_mempool(self):
        '''Batch call to Bitcoin Core for block count and mempool entries.

        Returns:
            blockcount - output of proxy.getblockcount()
            mempool - output of proxy.getrawmempool(verbose=True)
        '''
        with self.rlock:
            self._RawProxy__id_count += 1
            rpc_call_list = [
                {
                    'version': '1.1',
                    'method': 'getblockcount',
                    'params': [],
                    'id': self._RawProxy__id_count
                },
                {
                    'version': '1.1',
                    'method': 'getrawmempool',
                    'params': [True],
                    'id': self._RawProxy__id_count
                }
            ]

            responses = self._batch(rpc_call_list)
            for response in responses:
                if response['error']:
                    raise JSONRPCException(response['error'])
                if 'result' not in response:
                    raise JSONRPCException({
                        'code': -343, 'message': 'missing JSON-RPC result'
                    })

            return responses[0]['result'], responses[1]['result']


class DataSample(object):
    '''Container for 1-D numerical data with methods to compute statistics.'''

    def __init__(self, datapoints=None):
        '''Specify the initial datapoints with an iterable.'''
        if datapoints:
            self.datapoints = sorted(datapoints)
            self.n = len(self.datapoints)
        else:
            self.datapoints = []
            self.n = None
        self.mean = None
        self.std = None
        self.mean_interval = None

    def add_datapoints(self, datapoints):
        '''Add additional datapoints with an iterable.'''
        for d in datapoints:
            insort(self.datapoints, d)
        self.n = len(self.datapoints)

    def calc_stats(self):
        '''Compute various statistics of the data.

        mean - sample mean
        std - sample standard deviation
        mean_interval - 95% confidence interval for the sample mean, using a
                        normal approximation.
        '''
        if self.n < 2:
            raise ValueError("Need at least 2 datapoints.")
        self.mean = float(sum(self.datapoints)) / self.n
        variance = (sum([(d - self.mean)**2 for d in self.datapoints]) /
                    (self.n - 1))
        self.std = variance**0.5
        half_interval = 1.96*(variance/self.n)**0.5
        self.mean_interval = (self.mean - half_interval,
                              self.mean + half_interval)

    def get_percentile(self, p, weights=None):
        '''Returns the (p*100)th percentile of the data.

        Optional weights argument is a list specifying the weight of each
        datapoint for computing a weighted percentile.
        '''
        if p > 1 or p < 0:
            raise ValueError("p must be in [0, 1].")
        if not weights:
            return self.datapoints[max(int(ceil(p*self.n)) - 1, 0)]
        elif len(weights) == self.n:
            total = sum(weights)
            target = total*p
            curr_total = 0.
            for idx, s in enumerate(self.datapoints):
                curr_total += weights[idx]
                if curr_total >= target:
                    return s
        else:
            raise ValueError("len(weights) must be equal to len(datapoints).")

    def __repr__(self):
        return "n: %d, mean: %.2f, std: %.2f, interval: %s" % (
            self.n, self.mean, self.std, self.mean_interval)


class Table(object):
    def __init__(self, colwidths=None, padding=2):
        self.colwidths = colwidths
        self.rows = []
        self.justifs = []
        self.padding = padding

    def add_row(self, row, justifs=None):
        if self.colwidths is None:
            self.colwidths = [0]*len(row)
        newrow = [str(el) for el in row]
        self.rows.append(newrow)
        self._adjust_colwidths(newrow)

        if justifs is None:
            justifs = '>'*len(row)
        self.justifs.append(justifs)

    def _adjust_colwidths(self, row):
        for idx, el in enumerate(row):
            self.colwidths[idx] = max(len(el), self.colwidths[idx])

    def print_table(self):
        print("")
        for row, justifs in zip(self.rows, self.justifs):
            s = ''
            for just, colwidth in zip(justifs, self.colwidths):
                s += '{:' + just + str(colwidth + self.padding) + '}'
            print(s.format(*row))
        print("")


def save_obj(obj, filename):
    '''Convenience function to pickle an object to disk.'''
    with open(filename, 'wb') as f:
        pickle.dump(obj, f)


def load_obj(filename):
    '''Convenience function to unpickle an object from disk.'''
    with open(filename, 'rb') as f:
        obj = pickle.load(f)
    return obj


def get_coinbase_info(blockheight=None, block=None):
    '''Gets coinbase tag and addresses of a specified block.

    You can either specify a block height, or pass in a
    bitcoin.core.CBlock object.

    Keyword args:
        blockheight - height of block
        block - a bitcoin.core.CBlock object
    Returns:
        addresses - A list of p2sh/p2pkh addresses corresponding to the
                    outputs. Returns None in place of an unrecognizable
                    scriptPubKey.
        tag - the UTF-8 decoded scriptSig.
    Raises:
        Exceptions originating from bitcoin.rpc.Proxy, if there is a problem
        with JSON-RPC.
    '''
    if not block:
        block = proxy.getblock(proxy.getblockhash(blockheight))
    coinbase_tx = block.vtx[0]
    assert coinbase_tx.is_coinbase()
    addresses = []
    for output in coinbase_tx.vout:
        try:
            addr = str(CBitcoinAddress.from_scriptPubKey(output.scriptPubKey))
        except (CBitcoinAddressError, ValueError):
            addr = None
        else:
            addr = addr.decode('ascii')
        addresses.append(addr)

    tag = str(coinbase_tx.vin[0].scriptSig).decode('utf-8', 'ignore')

    return addresses, tag


def get_pph(blockheight=None, block=None):
    '''Get probability p of finding a block, per hash performed.'''
    if not block:
        block = proxy.getblock(proxy.getblockhash(blockheight))
    nbits = hex(block.nBits)[2:]
    assert len(nbits) == 8
    significand = int(nbits[2:], base=16)
    exponent = (int(nbits[:2], base=16)-3)*8
    logtarget = log(significand, 2) + exponent

    # This is not technically correct because the target is fulfilled in the
    # case of equality. It should more correctly be:
    # (2**logtarget + 1) / 2**256,
    # but of course the difference is negligible.
    return 2**(logtarget - 256)


def get_block_timestamp(blockheight):
    '''Get the timestamp of a block specified by height.'''
    block = proxy.getblock(proxy.getblockhash(blockheight))
    return block.nTime


def get_feerate(rawentry):
    '''Return the feerate of a mempool entry.
    rawentry is the dict returned by getrawmempool(verbose=True).
    '''
    return int(rawentry['fee']*COIN) * 1000 // rawentry['size']


def round_random(f):
    '''Random integer rounding.

    Returns a random integer in {floor(f), ceil(f)} with expected value
    equal to f.
    '''
    int_f = int(f)
    return int_f + (random() <= f - int_f)


def interpolate(x0, x, y):
    '''Linear interpolation of y = f(x) at x0.

     x is assumed sorted.

     Returns:
        y0 - Interpolated value of the function. If x0 is outside of the
             range of x, then return the boundary values.

        idx - The unique value such that x[idx-1] <= x0 < x[idx],
              if it exists. Otherwise idx = 0 if x0 < all values in x,
              and idx = len(x) if x0 >= all values in x.
     '''
    idx = bisect(x, x0)
    if idx == len(x):
        return y[-1], idx
    elif idx == 0:
        return y[0], idx
    else:
        x_f = x[idx]
        y_f = y[idx]
        x_b = x[idx-1]
        y_b = y[idx-1]
        y0 = y_b + (x0-x_b)/(x_f-x_b)*(y_f-y_b)

        return y0, idx


def try_wrap(fn):
    '''Decorator to try a function and log all exceptions without raising.'''
    @wraps(fn)
    def nicetry(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception('try_wrap exception')
    return nicetry


def itertimer(maxiters=None, maxtime=None, stopflag=None):
    '''Generator function which iterates till specified limits.

    maxiters - max number of iterations
    maxtime - max time in seconds that should be spent on the iteration
    stopflag - threading.Event() object. Stop iteration immediately if this
               is set.
    '''
    if maxiters is None:
        maxiters = float("inf")
    if maxtime is None:
        maxtime = float("inf")
    starttime = time()
    i = 0
    while True:
        elapsedtime = time() - starttime
        if stopflag and stopflag.is_set() or (
                elapsedtime > maxtime or i >= maxiters):
            break
        yield i, elapsedtime
        i += 1


proxy = BatchProxy()
