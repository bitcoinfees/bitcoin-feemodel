from __future__ import division

import threading
import Queue
import logging
import operator
import cPickle as pickle
from bisect import insort, bisect, bisect_left
from math import ceil, log
from contextlib import contextmanager
from random import random
from functools import wraps
from collections import OrderedDict
from itertools import izip
from copy import copy

from tabulate import tabulate

from bitcoin.rpc import Proxy, JSONRPCException
from bitcoin.wallet import CBitcoinAddress, CBitcoinAddressError
from bitcoin.core import COIN

import feemodel.config


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


def logexceptions(fn):
    """Decorator that logs exceptions.

    Logs all exceptions / stack traces in the decorated function before
    reraising.
    """
    @wraps(fn)
    def logged_fn(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.exception(
                "{} in {}.".format(e.__class__.__name__, fn.__name__))
            raise e
    return logged_fn


class WorkerThread(threading.Thread):
    """Worker thread.

    Fetches args from a queue and calls a user specified function.
    """

    STOP = StopIteration()

    def __init__(self, workfn):
        """workfn is the function to be called."""
        self.workfn = workfn
        self._workqueue = Queue.Queue()
        super(WorkerThread, self).__init__()

    @logexceptions
    def run(self):
        """Main loop."""
        while True:
            args = self._workqueue.get()
            if args is self.STOP:
                break
            self.workfn(*args)
        logger.info("{} worker stopped.".format(self.workfn.__name__))

    def put(self, *args):
        """Put an argument tuple into the queue.

        In the main loop, workfn will be called with these arguments.
        """
        self._workqueue.put(args)

    def stop(self):
        """Stop and join this worker thread."""
        self._workqueue.put(self.STOP)
        self.join()


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
    '''
    Container for i.i.d. random variates with
    methods to compute statistics.
    '''

    def __init__(self, datapoints=None):
        '''Specify the initial datapoints with an iterable.'''
        if datapoints:
            self.datapoints = sorted(datapoints)
        else:
            self.datapoints = []
        self.mean = None
        self.std = None
        self.mean_95ci = None

    def add_datapoints(self, datapoints):
        '''Add additional datapoints with an iterable.

        datapoints can also be a single point.
        '''
        try:
            dataiter = iter(datapoints)
        except TypeError:
            insort(self.datapoints, datapoints)
        else:
            self.datapoints.extend(dataiter)
            self.datapoints.sort()

    def calc_stats(self):
        '''Compute various statistics of the data.

        mean - sample mean
        std - sample standard deviation
        mean_interval - 95% confidence interval for the sample mean, using a
                        normal approximation.
        '''
        n = len(self.datapoints)
        if n < 2:
            raise ValueError("Need at least 2 datapoints.")
        self.mean = float(sum(self.datapoints)) / n
        variance = (sum([(d - self.mean)**2 for d in self.datapoints]) /
                    (n - 1))
        self.std = variance**0.5
        half_95ci = 1.96*(variance/n)**0.5
        self.mean_95ci = (self.mean - half_95ci, self.mean + half_95ci)

    def get_percentile(self, p, weights=None):
        '''Returns the (p*100)th percentile of the data.

        Optional weights argument is a list specifying the weight of each
        datapoint for computing a weighted percentile.
        '''
        if p > 1 or p < 0:
            raise ValueError("p must be in [0, 1].")
        n = len(self.datapoints)
        if not weights:
            return self.datapoints[max(int(ceil(p*n)) - 1, 0)]
        elif len(weights) == n:
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
        return "DataSample(n: {}, mean: {}, std: {}, mean_95ci: {})".format(
            len(self), self.mean, self.std, self.mean_95ci)


class Function(object):
    '''A (math) function object with interpolation methods.'''

    def __init__(self, x, y):
        '''y and x are lists s.t. y = f(x) and x is sorted.'''
        self._x = x
        self._y = y

    def __call__(self, x0, use_upper=False, use_lower=False):
        '''Evaluate the function at x0, by linear interpolation.

        use_upper and use_lower specifies what to do if x is outside
        the domain of the function [min(self._x), max(self._x)).

        if use_upper is True, then if all([x >= xi for xi in self._x]),
        return f(max(self._x)).

        if use_lower is True, then if all([x < xi for xi in self._x]),
        return f(min(self._x)).

        Otherwise return None, if x is outside the domain.
        '''
        y0, xidx = interpolate(x0, self._x, self._y)
        if xidx == 0:
            return y0 if use_lower else None
        if xidx == len(self._x):
            return y0 if use_upper or x0 == self._x[-1] else None
        return y0

    def inv(self, y0, use_upper=False, use_lower=False):
        '''Evaluate the inverse function at y0 by linear interpolation.

        use_upper and use_lower have the same meaning as in self.__call__.

        We don't check that the function is 1-to-1: if you call this method,
        it is assumed so.
        '''
        _y = self._y[:]
        _x = self._x[:]
        if _y[-1] < _y[0]:
            _y.reverse()
            _x.reverse()
        x0, yidx = interpolate(y0, _y, _x)
        if yidx == 0:
            return x0 if use_lower else None
        if yidx == len(_y):
            return x0 if use_upper or y0 == _y[-1] else None
        return x0

    def addpoint(self, xi, yi):
        if xi in self._x:
            return
        self._x, self._y = zip(*sorted(list(self) + [(xi, yi)]))

    def __getitem__(self, idx):
        return self._x[idx], self._y[idx]

    def __str__(self):
        headers = ['x', 'y']
        table = list(self)
        return tabulate(table, headers=headers)

    def __len__(self):
        return len(self._y)

    def __iter__(self):
        return izip(self._x, self._y)

    def __copy__(self):
        return Function(list(self._x), list(self._y))


class DiscreteFunction(Function):
    """Like Function but without the interpolation.

    Only defined on the specified x points.
    """

    def __call__(self, x0):
        try:
            idx = self._x.index(x0)
        except ValueError:
            raise ValueError("Not defined at this point")
        return self._y[idx]

    def inv(self, y0):
        try:
            idx = self._y.index(y0)
        except ValueError:
            raise ValueError("Not defined at this point")
        return self._x[idx]


class StepFunction(Function):
    """A non-negative, monotone step function.

    Points always represent the upper part of a discontinuity.
    As of now it works for the intended purpose, but it's a bit of a mess,
    sorry.
    """

    def __call__(self, x0):
        # TODO: need better specifications about the function.
        #       e.g. values outside of the defined domain, inc / dec etc.
        if len(self) < 2:
            raise ValueError("Function must have at least 2 points.")
        if self[-1][1] > self[0][1]:
            # Function is strictly increasing
            idx = bisect(self._x, x0) - 1
            if idx < 0:
                return 0
        else:
            # Function is weakly decreasing
            idx = bisect_left(self._x, x0)
            if idx == len(self._x):
                return 0
        return self._y[idx]

    def approx(self, percenterror=0.05, percentstep=0.05):
        """Approximate by a piecewise linear function.

        Use as few segments as possible to stay below a given allowable error.
        For convenience, errors are only defined on integer values of the
        domain (also because feerates are integer valued).
        """
        if len(self) < 2:
            raise ValueError("Function must have at least 2 points.")
        # Make a copy to mutate
        selfcopy = copy(self)
        reverse = False
        if selfcopy[-1][1] < selfcopy[0][1]:
            reverse = True
            # If self is decreasing, normalize it to be increasing
            selfcopy._x = map(operator.neg, reversed(selfcopy._x))
            selfcopy._y.reverse()

        selfmax = selfcopy._y[-1]
        error_thresh = percenterror*selfmax
        step = percentstep*selfmax
        f = Function([selfcopy._x[0]], [selfcopy._y[0]])
        previdx = 0
        prev_cand_idx = None
        idx = 0
        while idx < len(selfcopy):
            cand = selfcopy[idx]
            _f = copy(f)
            _f.addpoint(*cand)
            maxerror = selfcopy._get_maxerror(previdx, idx, _f)
            if maxerror > error_thresh:
                if prev_cand_idx is not None:
                    f.addpoint(*selfcopy[prev_cand_idx])
                    previdx = prev_cand_idx
                else:
                    x, y = selfcopy[idx]
                    f.addpoint(x-1, selfcopy(x-1))
                prev_cand_idx = None
            elif cand[1] - selfcopy[previdx][1] > step:
                f.addpoint(*cand)
                previdx = idx
                prev_cand_idx = None
                idx += 1
            else:
                prev_cand_idx = idx
                idx += 1
        if prev_cand_idx:
            f.addpoint(*selfcopy[prev_cand_idx])
        if reverse:
            f._x = map(operator.neg, reversed(f._x))
            f._y = list(reversed(f._y))
        return f

    def _get_maxerror(self, previdx, curridx, f):
        maxerror = 0
        for idx in range(previdx, curridx+1):
            x, y = self[idx]
            curr_error = abs(y - f(x))
            curr_error_back = abs(self(x-1) - f(x-1, use_lower=True))
            maxerror = max(curr_error, curr_error_back, maxerror)
        return maxerror

    def __copy__(self):
        return StepFunction(list(self._x), list(self._y))


class BlockMetadata(object):
    """Block metadata.

    Coinbase addresses / tag, block size, mean hashes per block
    (function of nBits).
    """

    def __init__(self, height):
        self.height = height
        self.addrs, self.tag = get_coinbase_info(height)
        self.size = get_block_size(height)
        self.hashes = get_hashesperblock(height)

    def get_poolname(self):
        pooltags = feemodel.config.pooltags
        assigned_name = None
        for name, taglist in pooltags.items():
            if any([tag in self.tag for tag in taglist]):
                if assigned_name is not None:
                    logger.warning("Multiple name assignment in block {}.".
                                   format(self.height))
                else:
                    assigned_name = name
        if assigned_name is None:
            for addr in self.addrs:
                if addr is not None:
                    assigned_name = addr[:12] + "_"
                    break
        if assigned_name is None:
            assigned_name = "UNKNOWN_" + str(self.height)

        return assigned_name

    def __repr__(self):
        return ("BlockMetaData(height: {}, size: {})".
                format(self.height, self.size))


def save_obj(obj, filename, protocol=2):
    '''Convenience function to pickle an object to disk.'''
    with open(filename, 'wb') as f:
        pickle.dump(obj, f, protocol=protocol)


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


# TODO: Deprecate in favour of BlockMetadata.get_poolname
def get_block_name(blockheight):
    """Get name of the pool which produced the block.

    Matches the block coinbase tag with pool tags in pooltags.json.
    """
    raise NotImplementedError
    pooltags = feemodel.config.pooltags
    cb_addrs, cb_tag = get_coinbase_info(blockheight)
    assigned_name = None
    for name, taglist in pooltags.items():
        if any([tag in cb_tag for tag in taglist]):
            if assigned_name is not None:
                logger.warning("Multiple name assignment in block {}.".
                               format(blockheight))
            else:
                assigned_name = name
    if assigned_name is None:
        cb_addrs.sort()
        for addr in cb_addrs:
            if addr is not None:
                assigned_name = addr[:12] + "_"
                break
    if assigned_name is None:
        assigned_name = "UNKNOWN_" + str(blockheight)

    return assigned_name


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


def cumsum_gen(iterable, base=0, mapfn=lambda x: x):
    """Cumulative sum generator.

    Returns a generator that yields the cumulative sum of a given iterable.

    base is the object that you begin summing from.

    mapfn is a function that is applied to each element of the iterable prior
    to the summation.
    """
    cumsum = base
    for item in iterable:
        cumsum += mapfn(item)
        yield cumsum


proxy = BatchProxy()
