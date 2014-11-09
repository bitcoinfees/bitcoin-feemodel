from bitcoin.core import CTransaction, b2lx
from bitcoin.rpc import Proxy
from bitcoin.core import b2lx
from time import time

# All txids are binary data

proxy = Proxy()
reorgGuard = 6

class TxMempoolEntry:
    def __init__(self, txid):
        tx = proxy.getrawtransaction(txid)
        self.tx = tx
        self.txidHex = b2lx(tx.GetHash())
        self.rcvtime = time()
        self.dependants = set() # Mempool transactions which depend on this one
        self.dependencies = set() # Mempool transactions which this tx depends on
        self.inputAmounts = [None]*len(tx.vin)
        self.inputBlockHeight = [None]*len(tx.vin)

        modTxSize = nTxSize = len(tx.serialize())
        currHeight = proxy.getblockcount()

        for txinIdx, txin in enumerate(tx.vin):
            offset = 41 + min(110, len(txin.scriptSig))
            if modTxSize > offset:
                modTxSize -= offset
            try: 
                # Use proxy.gettxout first, as it seems to be much faster.
                # If it fails, then txout is in the mempool; use getrawtransaction
                prevoutPoint = proxy.gettxout(txin.prevout, includemempool=False)
                self.inputAmounts[txinIdx] = prevoutPoint['txout'].nValue
                inputBlockHeightProposal = currHeight - prevoutPoint['confirmations'] + 1
                # We only commit the input block height if it has more than $reorgGuard confirmations
                # This is so that priority calculations won't be erroneous if there is a reorg of < $reorgGuard.
                # Priority is likely to be (slightly) wrong if there is a reorg > $reorgGuard blocks
                self.inputBlockHeight[txinIdx] = inputBlockHeightProposal if prevoutPoint['confirmations'] >= reorgGuard else None

            except IndexError:                
                prevoutTx = proxy.getrawtransaction(txin.prevout.hash, verbose=True)
                self.inputAmounts[txinIdx] = prevoutTx['tx'].vout[txin.prevout.n].nValue
                confirmations = prevoutTx.get('confirmations')
                self.inputBlockHeight[txinIdx] = (currHeight - confirmations + 1) if confirmations >= reorgGuard else None
        

        self.feeRate = (sum(self.inputAmounts) - sum([vout.nValue for vout in tx.vout])) / float(nTxSize) * 1000
        self.nTxSize = nTxSize
        self.modTxSize = modTxSize

    def computePriority(self, offset=0):
        dPriority = 0
        currHeight = proxy.getblockcount()
        for txinIdx, txin in enumerate(self.tx.vin):
            if self.inputBlockHeight[txinIdx]:
                dPriority += (currHeight-self.inputBlockHeight[txinIdx]+1-offset)*self.inputAmounts[txinIdx]
            else:
                try:
                    prevoutPoint = proxy.gettxout(txin.prevout, includemempool=False)
                    dPriority += (prevoutPoint['confirmations']-offset)*self.inputAmounts[txinIdx]
                except IndexError:
                    prevoutTx = proxy.getrawtransaction(txin.prevout.hash, verbose=True)
                    confirmations = prevoutTx.get('confirmations')
                    if confirmations:
                        dPriority += (confirmations-offset)*self.inputAmounts[txinIdx]

        return dPriority / self.modTxSize

    def updateInputBlockHeight(self):
        currHeight = proxy.getblockcount()
        for txinIdx, txin in enumerate(tx.vin):
            if not self.inputBlockHeight[txinIdx]:
                try: 
                    # Use proxy.gettxout first, as it seems to be much faster.
                    # If it fails, then txout is in the mempool; use getrawtransaction
                    prevoutPoint = proxy.gettxout(txin.prevout, includemempool=False)
                    inputBlockHeightProposal = currHeight - prevoutPoint['confirmations'] + 1
                    # We only commit the input block height if it has more than $reorgGuard confirmations
                    # This is so that priority calculations won't be erroneous if there is a reorg of < $reorgGuard.
                    # Priority is likely to be (slightly) wrong if there is a reorg > $reorgGuard blocks
                    self.inputBlockHeight[txinIdx] = inputBlockHeightProposal if prevoutPoint['confirmations'] >= reorgGuard else None
                except IndexError:                
                    prevoutTx = proxy.getrawtransaction(txin.prevout.hash, verbose=True)
                    confirmations = prevoutTx.get('confirmations')
                    inputBlockHeight[txinIdx] = (currHeight - confirmations + 1) if confirmations >= reorgGuard else None


class TxMempool:
    def __init__(self):
        txidList = proxy.getrawmempool()
        self.txpool = {txid: TxMempoolEntry(txid) for txid in txidList}

        for txid,txm in self.txpool.iteritems():
            for txin in txm.tx.vin:
                prevoutHash = txin.prevout.hash
                if prevoutHash in self.txpool:
                    txm.dependencies.add(prevoutHash)
                    self.txpool[prevoutHash].dependants.add(txid)

    def update(self):
        newIdSet = set(proxy.getrawmempool())
        oldIdSet = set(self.txpool)

        removedSet = oldIdSet - newIdSet

        for txid in removedSet:
            txm = self.txpool[txid]
            for dependant in txm.dependants:
                self.txpool[dependant].dependencies.discard(txid)
            for dependency in txm.dependencies:
                self.txpool[dependency].dependants.discard(txid)

            del self.txpool[txid]

        addedSet = newIdSet - oldIdSet
        addedDict = {txid: TxMempoolEntry(txid) for txid in addedSet}
        
        for txid, txm in self.txpool.iteritems():
            for txin in txm.tx.vin:
                prevoutHash = txin.prevout.hash
                if prevoutHash in addedDict:
                    txm.dependencies.add(prevoutHash)
                    addedDict[prevoutHash].dependants.add(txid)
        
        self.txpool.update(addedDict)

        for txid, txm in addedDict.iteritems():
            for txin in txm.tx.vin:
                prevoutHash = txin.prevout.hash
                if prevoutHash in self.txpool:
                    txm.dependencies.add(prevoutHash)
                    self.txpool[prevoutHash].dependants.add(txid)

        for txm in self.txpool.itervalues():
            txm.updateInputBlockHeight()

        return len(addedSet)-len(removedSet)

