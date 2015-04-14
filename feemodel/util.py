from __future__ import division

import threading
import logging
from bisect import insort, bisect
from math import ceil, log
from contextlib import contextmanager
from random import random
from functools import wraps
from time import time
from collections import OrderedDict
try:
    import cPickle as pickle
except ImportError:
    import pickle

from tabulate import tabulate

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
    def context_start(self):
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
    connection before re-raising. This enables one to continue using the
    same proxy object after the exception (if, for e.g. Bitcoin Core goes
    offline momentarily) - otherwise it might not work. This is a hack -
    I'm not sure what is the right way to enable this behavior.
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


class CacheProxy(BlockingProxy):
    '''Proxy which caches recent blocks and block hashes.'''

    def __init__(self, maxblocks=10, maxhashes=1000):
        super(CacheProxy, self).__init__()
        self.blockmap = OrderedDict()
        self.hashmap = OrderedDict()
        self.maxblocks = maxblocks
        self.maxhashes = maxhashes

    def getcache(self, d, key, maxitems, default_fn):
        with self.rlock:
            result = d.get(key)
            if result is not None:
                # Move the most recently accessed item to the front
                del d[key]
                d[key] = result
            else:
                result = default_fn(key)
                d[key] = result
                if len(d) > maxitems:
                    # Remove the least recently accessed item
                    d.popitem(last=False)
            return result

    def getblock(self, blockhash):
        block = self.getcache(self.blockmap, blockhash, self.maxblocks,
                              super(CacheProxy, self).getblock)
        return block

    def getblockhash(self, blockheight):
        blockhash = self.getcache(self.hashmap, blockheight, self.maxhashes,
                                  super(CacheProxy, self).getblockhash)
        return blockhash


class BatchProxy(CacheProxy):
    '''Proxy with batch calls.'''

    def poll_mempool(self):
        '''Batch call to Bitcoin Core for block count and mempool entries.

        Returns:
            blockcount - output of proxy.getblockcount()
            mempool - output of proxy.getrawmempool(verbose=True)
        '''
        with self.rlock:
            try:
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
            except Exception as e:
                self.close()
                raise e


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

    def __len__(self):
        return len(self.datapoints)

    def __repr__(self):
        # FIXME: repr breaks before self.calc_stats is run
        return "n: %d, mean: %.2f, std: %.2f, interval: %s" % (
            self.n, self.mean, self.std, self.mean_interval)


class Function(object):
    '''A (math) function object with interpolation methods.'''

    def __init__(self, x, y):
        '''y and x are lists s.t. y = f(x) and x is sorted.'''
        self._x = x
        self._y = y

    def __call__(self, x, use_upper=False, use_lower=False):
        '''Evaluate the function at x, by linear interpolation.

        use_upper and use_lower specifies what to do if x is outside
        the domain of the function [min(self._x), max(self._x)).

        if use_upper is True, then if all([x >= xi for xi in self._x]),
        return f(max(self._x)).

        if use_lower is True, then if all([x < xi for xi in self._x]),
        return f(min(self._x)).

        Otherwise return None, if x is outside the domain.
        '''
        y, xidx = interpolate(x, self._x, self._y)
        if xidx == 0:
            return y if use_lower else None
        if xidx == len(self._x):
            return y if use_upper or x == self._x[-1] else None
        return y

    def inv(self, y, use_upper=False, use_lower=False):
        '''Evaluate the inverse function at y by linear interpolation.

        use_upper and use_lower have the same meaning as in self.__call__.

        We don't check that the function is 1-to-1: if you call this method,
        it is assumed so.
        '''
        _y = self._y[:]
        _x = self._x[:]
        if _y[-1] < _y[0]:
            _y.reverse()
            _x.reverse()
        x, yidx = interpolate(y, _y, _x)
        if yidx == 0:
            return x if use_lower else None
        if yidx == len(_y):
            return x if use_upper or y == _y[-1] else None
        return x

    def print_fn(self):
        headers = ['x', 'y']
        table = zip(self._x, self._y)
        print(tabulate(table, headers=headers))

    def __len__(self):
        return len(self._y)

    def __iter__(self):
        return iter(zip(self._x, self._y))


# TODO: deprecate this
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


def get_coinbase_info(blockheight):
    '''Gets coinbase tag and addresses of a block with specified height.

    Returns:
        addresses - A list of p2sh/p2pkh addresses corresponding to the
                    outputs. Returns None in place of an unrecognizable
                    scriptPubKey.
        tag - the UTF-8 decoded scriptSig.
    Raises:
        Exceptions originating from bitcoin.rpc.Proxy, if there is a problem
        with JSON-RPC.
    '''
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


# TODO: deprecate this in favour of get_hashesperblock
def get_pph(blockheight):
    '''Get probability p of finding a block, per hash performed.'''
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


def get_hashesperblock(blockheight):
    '''Get the expected number of hashes required per block.'''
    block = proxy.getblock(proxy.getblockhash(blockheight))
    nbits = hex(block.nBits)[2:]
    assert len(nbits) == 8
    significand = int(nbits[2:], base=16)
    exponent = (int(nbits[:2], base=16)-3)*8
    logtarget = log(significand, 2) + exponent

    # This is not technically correct because the target is fulfilled in the
    # case of equality. It should more correctly be:
    # 2**256 / (2**logtarget + 1),
    # but of course the difference is negligible.
    return 2**(256 - logtarget)


def get_block_timestamp(blockheight):
    '''Get the timestamp of a block specified by height.'''
    block = proxy.getblock(proxy.getblockhash(blockheight))
    return block.nTime


def get_block_size(blockheight):
    '''Get the size of a block specified by height, in bytes.'''
    block = proxy.getblock(proxy.getblockhash(blockheight))
    return len(block.serialize())


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


def cumsum_gen(seq, base=0, mapfn=None):
    """Cumulative sum generator.

    Returns a generator that yields the cumulative sum of a given sequence.

    base is the object that you begin summing from.

    mapfn is a function that is applied to each element of the sequeunce prior
    to the summation.
    """
    def identity(x):
        return x

    if mapfn is None:
        mapfn = identity

    cumsum = base
    for item in seq:
        cumsum += mapfn(item)
        yield cumsum


# TODO: Deprecate this and itertimer
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
