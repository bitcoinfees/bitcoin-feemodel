import os, json
import cPickle as pickle
from time import sleep, time
from txmempool import TxMempool
from bitcoin.core import b2lx
import shelve
from model.config import config
from model.util import logWrite, proxy
from model.em import writeEMData

statVersion = '0.2'

# datadir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data/')
# configPath = os.path.join(os.path.dirname(__file__), '../../config.json')
# logPath = os.path.join(os.path.dirname(__file__), 'debug.log')

# try:
#     with open(configPath, 'r') as configFile:
#         config = json.load(configFile) 
# except IOError:
#     raise IOError("No config.json found.")

datadir = os.path.normpath(config['collectdata']['datadir'])
pollperiod =  config['collectdata']['pollperiod']
minFeeRate = config['collectdata']['defaultMinFeeRate']
timedeltaMargin = config['collectdata']['timedeltaMargin']
defaultMinTime = config['collectdata']['defaultMinTime']
priorityThresh = config['collectdata']['priorityThresh']

def collect():

    if not os.path.exists(datadir):
        os.mkdir(datadir)

    # got to change this. can't use shelf; buggy
    shelfFile = os.path.join(datadir, 'blockstats_v' + str(statVersion))
    # shelf = shelve.open(shelfFile)

    shelfDebugFile = os.path.join(datadir, 'debugstats_v' + str(statVersion))
    # shelfDebug = shelve.open(shelfDebugFile)

    mempool = TxMempool()
    mempool.update()

    prevHeight = proxy.getblockcount()
    mempool.update()
    sleep(pollperiod)
    # try:
    while True:
        currHeight = proxy.getblockcount()
        if currHeight == prevHeight:
            txDelta, _discard1, _discard2 = mempool.update()
            if txDelta < 0:
                logWrite('Warning, mempool entries removed when no new block was found.')
            sleep(pollperiod)
        else:
            numNewBlocks = currHeight - prevHeight
            blocks = []
            
            for blockHeight in range(prevHeight+1, currHeight+1):
                blockdata = proxy.getblock(proxy.getblockhash(blockHeight))
                blocks.append(Block(blockdata, mempool, blockHeight, currHeight))

            prevHeight = proxy.getblockcount()
            if prevHeight != currHeight:
                logWrite('Blocks are coming too quickly, we skipped one here.')

            _discard1, removedSet, _discard2 = mempool.update()
            removedSet = map(b2lx, removedSet)

            for block in blocks:
                block.writeData(shelfFile, removedSet, shelfDebugFile=shelfDebugFile)

            sleep(pollperiod)
    
    # except KeyboardInterrupt:
    #     logWrite("Keyboard Interrupted")
    # except Exception as e:
    #     print e.message, e.__doc__
    # finally:
    #     shelf.close()
    #     shelfDebug.close()

class Block:
    def __init__(self, blockdata, mempool, blockHeight, currHeight):
        self.blockHeight = blockHeight
        self.blockTxList = [tx.GetHash() for tx in blockdata.vtx]
        self.nTime = time()
        self.blockSize = len(blockdata.serialize())
        self.numTxs = len(self.blockTxList) - 1
        self.processStats(mempool, currHeight)

    def processStats(self, mempool, currHeight):
        self.stats = [{
            'txid': txm.txidHex,
            'inBlock': txid in self.blockTxList,
            'feeRate': txm.feeRate,
            'priority': txm.computePriority(currHeight=currHeight, offset=currHeight-self.blockHeight+1),
            'size': txm.nTxSize,
            'dependants': map(b2lx, txm.dependants),
            'dependencies': map(b2lx, txm.dependencies),
            'timedelta': self.nTime-txm.nTime
        } for txid,txm in mempool.txpool.iteritems()]

        mempool.deleteTx(self.blockTxList, currHeight=currHeight)

    def writeData(self, shelfFile, removedSet, shelfDebugFile=None):
        numMempoolTxsInBlock = len([1 for tx in self.stats if tx['inBlock']])

        try:
            mintime = min([tx['timedelta'] for tx in self.stats if tx['inBlock']])
        except ValueError:
            mintime = defaultMinTime

        mintime += timedeltaMargin

        stat_filt = [tx for tx in self.stats
            # If it was removed without getting into a block, then it was invalidated; don't count it
            if (tx['inBlock'] or not tx['txid'] in removedSet) 
            # Discard txs near to time of block discovery
            and tx['timedelta'] > mintime
            # If any of the tx's mempool deps are not inBlock, its statistics are irrelevant.
            and self._depsCheck(tx)]

        stat_fee = filter(lambda x: x['feeRate'], stat_filt)
        stat_fee.sort(key=lambda x: x['feeRate'], reverse=True)
        stat_priority = filter(lambda x: x['priority'] > priorityThresh
            and x['feeRate'] < minFeeRate, stat_filt)
        stat_priority.sort(key=lambda x: x['priority'], reverse=True)

        # Now for the algorithm-specific data writes
        writeEMData(shelfFile, stat_fee, stat_priority, self.blockHeight, self.blockSize, mintime-defaultMinTime)

        if not shelfDebugFile is None:
            shelfDebug = shelve.open(shelfDebugFile)
            shelfDebug[str(self.blockHeight)] = self.stats
            shelfDebug.close()

        logWrite(str(numMempoolTxsInBlock) + ' of ' + 
            str(self.numTxs) + ' in block ' + str(self.blockHeight))

    def _depsCheck(self, tx):
        deps = [depc for depc in self.stats if depc['txid'] in tx['dependencies']]
        return all([dep['inBlock'] for dep in deps])




