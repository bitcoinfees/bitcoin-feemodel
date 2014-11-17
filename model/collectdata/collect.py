import os, json
import cPickle as pickle
from time import sleep, time
from txmempool import TxMempool
from bitcoin.core import b2lx
import shelve
from model.config import config
from model.util import logWrite, proxy

statVersion = '0.1'

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

if not os.path.exists(datadir):
    os.mkdir(datadir)

shelfFile = os.path.join(datadir, 'blockstats_v' + str(statVersion))
shelf = shelve.open(shelfFile)

def collect():

    mempool = TxMempool()
    mempool.update()

    prevHeight = proxy.getblockcount()
    mempool.update()
    sleep(pollperiod)
    try:
        while True:
            currHeight = proxy.getblockcount()
            if currHeight == prevHeight:
                txDelta, _discard1, _discard2 = mempool.update()
                if txDelta < 0:
                    logWrite('Warning, mempool entries removed when no new block was found.')
                sleep(pollperiod)
            else:
                numNewBlocks = currHeight - prevHeight
                mempoolStats = {}
                blockTxList = {}
                for blockHeight in range(prevHeight+1, currHeight+1):
                    block = proxy.getblock(proxy.getblockhash(blockHeight))
                    blockRcvTime = time()
                    blockTxList[blockHeight] = [tx.GetHash() for tx in block.vtx]
                   
                    mempoolStats[blockHeight] = [{
                        'txid': txm.txidHex,
                        'inBlock': txid in blockTxList[blockHeight],
                        'feeRate': txm.feeRate,
                        'priority': txm.computePriority(currHeight=currHeight, offset=currHeight-blockHeight+1),
                        'size': txm.nTxSize,
                        'dependants': map(b2lx, txm.dependants),
                        'dependencies': map(b2lx, txm.dependencies),
                        'timedelta': blockRcvTime-txm.rcvTime
                    } for txid,txm in mempool.txpool.iteritems()]
    
                    mempool.deleteTx(blockTxList[blockHeight],currHeight=currHeight)

                prevHeight = proxy.getblockcount()
                if prevHeight != currHeight:
                    logWrite('Blocks are coming too quickly, we skipped one here.')

                _discard1, removedSet, _discard2 = mempool.update()
                removedSet = map(b2lx, removedSet)
                for stat in mempoolStats.values():
                    for tx in stat:
                        if not tx['inBlock'] and tx['txid'] in removedSet:
                            # A tx that did not get included in a block, yet got removed from mempool:
                            # Means that it was invalidated by the latest blocks (double spent), so 
                            # we don't want to use it in the fee estimations.
                            stat.remove(tx)

                for blockHeight, stat in mempoolStats.items():
                    numTxsInBlock = len(blockTxList[blockHeight])-1
                    numMempoolTxsInBlock = len([1 for tx in stat if tx['inBlock']])
                    logWrite(str(numMempoolTxsInBlock) + ' of ' + str(numTxsInBlock) + ' in block ' + str(blockHeight))

                    shelf[str(blockHeight)] = stat
                    # with open(os.path.join(datadir, str(blockHeight)+'v'+str(statVersion)+'.pickle'),'wb') as dataFile:
                    #     pickle.dump(stat, dataFile)

                sleep(pollperiod)
    
    except KeyboardInterrupt:
        logWrite("Keyboard Interrupt")
    except Exception as e:
        print e.message, e.__doc__
    finally:
        shelf.close()


