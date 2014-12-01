import os, json
import cPickle as pickle
from time import sleep, time
from txmempool import TxMempool
from bitcoin.core import b2lx
from model.config import config, dbFile
from model.util import logWrite, proxy
# from model.em import writeEMData
import sqlite3

# datadir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data/')
# configPath = os.path.join(os.path.dirname(__file__), '../../config.json')
# logPath = os.path.join(os.path.dirname(__file__), 'debug.log')

# try:
#     with open(configPath, 'r') as configFile:
#         config = json.load(configFile) 
# except IOError:
#     raise IOError("No config.json found.")

# datadir = os.path.abspath(os.path.dirname(dbFile))
datadir = config['collectdata']['datadir']
statVersion = config['collectdata']['statVersion']
pollperiod =  config['collectdata']['pollperiod']
minFeeRate = config['collectdata']['defaultMinFeeRate']
timedeltaMargin = config['collectdata']['timedeltaMargin']
defaultMinTime = config['collectdata']['defaultMinTime']
priorityThresh = config['collectdata']['priorityThresh']

def collect(debug=False):

    if not os.path.exists(datadir):
        os.mkdir(datadir)

    try: 
        dbFileExists = os.path.isfile(dbFile)
        db = sqlite3.connect(dbFile)   

        if not dbFileExists:
            dbcur = db.cursor()
            dbcur.execute('CREATE TABLE fees \
                (blockheight INTEGER, feerate INTEGER, inblock INTEGER, size INTEGER, priority REAL)')
            dbcur.execute('CREATE TABLE priority \
                (blockheight INTEGER, priority REAL, inblock INTEGER, size INTEGER, feerate INTEGER)')
            dbcur.execute('CREATE TABLE block \
                (blockheight INTEGER, blocksize INTEGER, mintime REAL)')

        # got to change this. can't use shelf; buggy
        # shelfFile = os.path.join(datadir, 'blockstats_v' + str(statVersion))
        # shelf = shelve.open(shelfFile)

        # shelfDebugFile = os.path.join(datadir, 'debugstats_v' + str(statVersion))
        # shelfDebug = shelve.open(shelfDebugFile)

        mempool = TxMempool()
        mempool.update()

        prevHeight = proxy.getblockcount()
        mempool.update()
        sleep(pollperiod)
        
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
                    block.writeData(db, removedSet, debug=debug)

                sleep(pollperiod)
    
    except KeyboardInterrupt:
        logWrite("Halted due to keyboard interrupt.")
    except Exception as e:
        print e.message, e.__doc__
    finally:
        db.close()

    
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

    def writeData(self, db, removedSet, debug):
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
        # stat_fee.sort(key=lambda x: x['feeRate'], reverse=True)
        stat_priority = filter(lambda x: x['priority'] > priorityThresh, stat_filt)
        # stat_priority.sort(key=lambda x: x['priority'], reverse=True)

        dbcur = db.cursor()
        dbcur.executemany('INSERT INTO fees VALUES (?,?,?,?,?)',
            [(self.blockHeight, tx['feeRate'], tx['inBlock'], tx['size'], tx['priority']) for tx in stat_fee])
        dbcur.executemany('INSERT INTO priority VALUES (?,?,?,?,?)',
            [(self.blockHeight, tx['priority'], tx['inBlock'], tx['size'], tx['feeRate']) for tx in stat_priority])
        dbcur.execute('INSERT INTO block VALUES (?,?,?)',
            (self.blockHeight, self.blockSize, mintime-timedeltaMargin))

        db.commit()

        if debug:
            debugPath = os.path.join(datadir, str(self.blockHeight)+'_'+statVersion+'.pickle')
            with open(debugPath,'wb') as debugFile:
                pickle.dump(self.stats, debugFile)
        # Now for the algorithm-specific data writes
        # writeEMData(shelfFile, stat_fee, stat_priority, self.blockHeight, self.blockSize, mintime-defaultMinTime)

        # if not shelfDebugFile is None:
        #     shelfDebug = shelve.open(shelfDebugFile)
        #     shelfDebug[str(self.blockHeight)] = self.stats
        #     shelfDebug.close()

        logWrite(str(numMempoolTxsInBlock) + ' of ' + 
            str(self.numTxs) + ' in block ' + str(self.blockHeight))

    def _depsCheck(self, tx):
        deps = [depc for depc in self.stats if depc['txid'] in tx['dependencies']]
        return all([dep['inBlock'] for dep in deps])




