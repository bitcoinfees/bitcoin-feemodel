from bitcoin.core import b2lx
from minermodel.util import logWrite, proxy
from time import time

class Prevout:
    def __init__(self,prevout,currHeight):
        try: 
            # Use proxy.gettxout first, as it seems to be much faster.
            # If it fails, then txout is in the mempool; use getrawtransaction
            prevoutPoint = proxy.gettxout(prevout, includemempool=False)
            nValue = prevoutPoint['txout'].nValue
            confirmations = prevoutPoint.get('confirmations')
        except IndexError:
            prevoutTx = proxy.getrawtransaction(prevout.hash, verbose=True)
            nValue = prevoutTx['tx'].vout[prevout.n].nValue
            confirmations = prevoutTx.get('confirmations')

        self.prevout = prevout
        self.txid = prevout.hash
        self.nValue = nValue
        self.blockHeight = ((currHeight-confirmations+1) 
            if confirmations > 0 else None)

    def getCoinAge(self, currHeight, offset):
        if self.blockHeight:
            return (currHeight-self.blockHeight+1-offset)*self.nValue
        else:
            return 0

    def updateBlockHeight(self, currHeight):
        try: 
            prevoutPoint = proxy.gettxout(self.prevout, includemempool=False)
            confirmations = prevoutPoint.get('confirmations')
        except IndexError:                
            prevoutTx = proxy.getrawtransaction(self.txid, verbose=True)
            confirmations = prevoutTx.get('confirmations')

        self.blockHeight = ((currHeight-confirmations+1) 
            if confirmations > 0 else None)


class TxMempoolEntry:
    def __init__(self, txid, currHeight=None):
        tx = proxy.getrawtransaction(txid)
        self.tx = tx
        self.txid = tx.GetHash()
        self.txidHex = b2lx(self.txid)
        self.nTime = time()
        self.dependants = set() # Mempool transactions which depend on this one
        self.dependencies = set() # Mempool transactions which this tx depends on
        self.prevouts = [0]*len(tx.vin)

        if not currHeight:
            currHeight = proxy.getblockcount()

        nModSize = nTxSize = len(tx.serialize())

        for txinIdx, txin in enumerate(tx.vin):
            offset = 41 + min(110, len(txin.scriptSig))
            if nModSize > offset:
                nModSize -= offset
            self.prevouts[txinIdx] = Prevout(txin.prevout, currHeight)
        
        inputAmounts = sum([prevout.nValue for prevout in self.prevouts])
        outputAmounts = sum([vout.nValue for vout in tx.vout])
        self.feeRate = (inputAmounts - outputAmounts) * 1000 / nTxSize
        self.nTxSize = nTxSize
        self.nModSize = nModSize

    def computePriority(self, currHeight=None, offset=0):
        if not currHeight:
            currHeight = proxy.getblockcount()

        dPriority = sum([prevout.getCoinAge(currHeight,offset) 
            for prevout in self.prevouts])

        return float(dPriority) / self.nModSize

    def updateInputBlockHeights(self, currHeight=None):
        if not currHeight:
            currHeight = proxy.getblockcount()

        for idx,prevout in enumerate(self.prevouts):
            if not prevout.blockHeight:
                try:
                    prevout.updateBlockHeight(currHeight)
                except IndexError:
                    logWrite('Error updating input block height for tx ' + 
                        self.txidHex + ' input ' + str(idx))


class TxMempool:
    def __init__(self):
        txidList = proxy.getrawmempool()
        self.currHeight = proxy.getblockcount()
        self.txpool = {}
        for txid in txidList:
            try:
                self.txpool[txid] = TxMempoolEntry(txid, self.currHeight)
            except IndexError:
                logWrite('Error in fetching tx ' + b2lx(txid))

        for txid,txm in self.txpool.iteritems():
            for txin in txm.tx.vin:
                prevoutHash = txin.prevout.hash
                if prevoutHash in self.txpool:
                    txm.dependencies.add(prevoutHash)
                    self.txpool[prevoutHash].dependants.add(txid)

    def update(self):
        self.currHeight = proxy.getblockcount()

        newIdSet = set(proxy.getrawmempool())
        oldIdSet = set(self.txpool)

        removedSet = oldIdSet - newIdSet

        self.deleteTx(removedSet, currHeight=self.currHeight)

        addedSet = newIdSet - oldIdSet
        addedDict = {}
        for txid in addedSet:
            try:
                addedDict[txid] = TxMempoolEntry(txid, self.currHeight)
            except IndexError:
                logWrite('Error in fetching tx ' + b2lx(txid))
        
        for txid, txm in self.txpool.iteritems():
            for txin in txm.tx.vin:
                prevoutHash = txin.prevout.hash
                if prevoutHash in addedDict:
                    logWrite("A reorg must have happened.")
                    txm.updateInputBlockHeights(currHeight=self.currHeight)
                    txm.dependencies.add(prevoutHash)
                    addedDict[prevoutHash].dependants.add(txid)
        
        self.txpool.update(addedDict)

        for txid, txm in addedDict.iteritems():
            for txin in txm.tx.vin:
                prevoutHash = txin.prevout.hash
                if prevoutHash in self.txpool:
                    txm.dependencies.add(prevoutHash)
                    self.txpool[prevoutHash].dependants.add(txid)
        
        return len(addedSet)-len(removedSet), removedSet, addedSet

    def deleteTx(self,txidList,currHeight):
        updateInputBlockHeightList = set()
        for txid in txidList:
            txm = self.txpool.get(txid)
            if txm:
                for dependant in txm.dependants:
                    self.txpool[dependant].dependencies.discard(txid)
                    updateInputBlockHeightList.add(dependant)
                for dependency in txm.dependencies:
                    self.txpool[dependency].dependants.discard(txid)

                del self.txpool[txid]

        for dependant in updateInputBlockHeightList:
            txm = self.txpool.get(dependant)
            if txm:
                txm.updateInputBlockHeights(currHeight)