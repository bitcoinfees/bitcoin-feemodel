from __future__ import division

import click
from feemodel.apiclient import client


@click.group()
@click.option('--port', type=click.INT, default=None)
def cli(port):
    if port is not None:
        client.port = port


@cli.command()
@click.option('--mempool', is_flag=True,
              help='Collect memblock data only (no simulation)')
def start(mempool):
    '''Start the simulation app.

    Use --mempool for memblock data collection only (no simulation).
    '''
    # TODO: add "raw" output option for all commands
    from feemodel.app.main import main, logfile
    from feemodel.config import __version__, pkgname
    click.echo("{} {}".format(pkgname, __version__))
    if mempool:
        click.echo("Starting mempool data collection; logging to %s"
                   % logfile)
    else:
        click.echo("Starting simulation app; logging to %s" % logfile)
    main(mempool_only=mempool)


@cli.command()
def pools():
    '''Get mining pool statistics.

    *** COLUMNS ***

    \b
    [Name]  Name of the pool, as specified by blockchain.info's pools.json.
            If pool is unidentified, the name is the first valid coinbase
            payout address.

    [HR]    Hashrate in Terahashes per second.

    [Prop]  Proportion of the total hashrate.

    [MBS]   Max block size of the pool.

    [MFR]   Min fee rate of the pool.

    \b
    [AKN]   "Above k and n" values. The number of transactions (k) that were
            above the min fee rate and were included in their respective block,
            compared to the total number of transactions (n).

    \b
    [BKN]   "Below k and n" values. Identical to AKN but for transactions below
            the min fee rate.

    ***
    The following cols relate to the sampling distribution of the MFR
    estimator, obtained using bootstrap resampling.

    [MFR.mean]  Expected value of the MFR estimator.

    [MFR.std]   Standard deviation of the MFR estimator.

    [MFR.bias]  Bias of the MFR estimator.

    *** MISC STATS ***

    Timestamp: Date/time at which the estimates were computed, in local time.

    Block interval: The estimated mean time between blocks.
    '''
    import time
    from tabulate import tabulate
    from itertools import groupby
    from feemodel.util import cumsum_gen
    try:
        stats = client.get_pools()
    except Exception as e:
        click.echo(repr(e))
        return
    params = stats['params']
    table = [(paramname, param) for paramname, param in params.items()]
    click.echo("Params:")
    click.echo(tabulate(table))
    if 'pools' not in stats:
        # No valid estimates ready.
        click.echo(
            "Block shortfall of {}; trying next update at {}".
            format(stats['block_shortfall'], time.ctime(stats['next_update'])))
        return

    pools = stats['pools']
    headers = [
        'Name',
        'HR (Thps)',
        'Prop',
        'MBS',
        'MFR',
        'AKN',
        'BKN',
        'MFR.mean',
        'MFR.std',
        'MFR.bias'
    ]
    table = []
    pitems = sorted(pools.items(),
                    key=lambda p: p[1]['proportion'], reverse=True)
    for name, pool in pitems:
        row = [
            name,
            pool['hashrate']*1e-12,
            pool['proportion'],
            pool['maxblocksize'],
            pool['minfeerate'],
            pool['abovekn'],
            pool['belowkn'],
            pool['mfrmean'],
            pool['mfrstd'],
            pool['mfrbias'],
        ]
        table.append(row)
    click.echo('')
    click.echo(tabulate(table, headers=headers))
    click.echo('')

    def mfr_keyfn(poolitem):
        return poolitem[1]['minfeerate']

    def sumgroupbyterates(grouptuple):
        feerate, feegroup = grouptuple
        blockrate = 1 / stats['blockinterval']
        totalhashrate = stats['totalhashrate']
        groupbyterate = sum([
            pool['hashrate']*pool['maxblocksize']
            for name, pool in feegroup]) * blockrate / totalhashrate
        return (feerate, groupbyterate)

    pitems = filter(lambda pitem: pitem[1]['minfeerate'] < float("inf"),
                    sorted(pitems, key=mfr_keyfn))
    byterate_by_fee = map(sumgroupbyterates, groupby(pitems, mfr_keyfn))
    feerates, byterates = zip(*byterate_by_fee)
    maxbyterate = sum(byterates)
    rate_delta = maxbyterate / 50
    table = []
    next_rate = rate_delta
    for feerate, cumbyterate in zip(feerates, cumsum_gen(byterates)):
        if cumbyterate < next_rate:
            continue
        table.append((feerate, cumbyterate))
        next_rate = min(cumbyterate + rate_delta, maxbyterate)

    headers = ['Feerate', 'Capacity (bytes/s)']
    click.echo("Cumul. Capacity")
    click.echo("===============================")
    click.echo(tabulate(table, headers=headers))
    click.echo("")

    table = [
        ("Total hashrate (Thps)", stats['totalhashrate']*1e-12),
        ("Block interval", stats['blockinterval']),
        ("Timestamp", time.ctime(stats['timestamp'])),
        ("Next update", time.ctime(stats['next_update']))
    ]
    click.echo(tabulate(table))


@cli.command()
def transient():
    '''Get transient simulation statistics.'''
    import time
    from tabulate import tabulate

    try:
        stats = client.get_transient()
    except Exception as e:
        click.echo(repr(e))
        return
    params = stats['params']
    table = [(paramname, param) for paramname, param in params.items()]
    click.echo("Params:")
    click.echo(tabulate(table))

    if 'expectedwaits' not in stats:
        click.echo("No stats at this time.")
        return

    headers = [
        'Feerate',
        'Wait (s)',
        'Std Error (s)']
    table = zip(
        stats['feepoints'],
        stats['expectedwaits'],
        stats['expectedwaits_stderr'],)
    click.echo("")
    click.echo("Expected Wait by Feerate")
    click.echo("===========================")
    click.echo(tabulate(table, headers=headers))

    click.echo('')
    table = [
        ("Timestamp", time.ctime(stats['timestamp'])),
        ("Timespent", stats['timespent']),
        ("Num iters", stats['numiters'])
    ]
    click.echo(tabulate(table))


@cli.command()
def prediction():
    '''Get prediction scores.'''
    from tabulate import tabulate
    try:
        stats = client.get_prediction()
    except Exception as e:
        click.echo(repr(e))
        return
    params = stats['params']
    table = [(paramname, param) for paramname, param in params.items()]
    click.echo("Params:")
    click.echo(tabulate(table))

    if 'pval_ecdf' not in stats:
        click.echo("No stats at this time.")
        return

    headers = ['x', 'y']
    table = zip(*stats['pval_ecdf'])
    click.echo("")
    click.echo("P-Value ECDF")
    click.echo("===============")
    click.echo(tabulate(table, headers=headers))
    click.echo('')

    table = [
        ("p-distance", stats['pdistance']),
        ("Num txs", stats['numtxs'])
    ]
    click.echo(tabulate(table))


@cli.command()
def txrate():
    """Get tx rate statistics."""
    from tabulate import tabulate
    try:
        stats = client.get_txrate()
    except Exception as e:
        click.echo(repr(e))
        return

    params = stats['params']
    table = [(paramname, param) for paramname, param in params.items()]
    click.echo("Params:")
    click.echo(tabulate(table))

    if 'txrate' not in stats:
        click.echo("No stats at this time.")
        return

    headers = ['Feerate', 'bytes/s']
    table = zip(stats['cumbyterate']['feerates'],
                stats['cumbyterate']['byterates'])
    click.echo('')
    click.echo('Cumul. Tx Byterate')
    click.echo("====================")
    click.echo(tabulate(table, headers=headers))
    click.echo('')

    table = [
        ("Sample size", stats['samplesize']),
        ("Total time", stats['totaltime']),
        ("Tx rate", stats['txrate']),
        ("Expected byterate", stats['expected_byterate']),
        ("Expected byterate std", stats['expected_byterate_std']),
        ("Byterate with fee", stats['ratewithfee']),
    ]
    click.echo(tabulate(table))


@cli.command()
@click.argument('waittime', type=click.INT, required=True)
def estimatefee(waittime):
    '''Feerate estimation.

    Estimate feerate (satoshis) to have an average wait time of
    WAITTIME minutes.
    '''
    try:
        res = client.estimatefee(waittime)
    except Exception as e:
        click.echo(repr(e))
        return
    click.echo(res['feerate'])


@cli.command()
@click.option("--waitcostfn", "-w",
              type=click.Choice(['linear', 'quadratic']),
              default="quadratic")
@click.argument("txsize", type=click.INT, required=True)
@click.argument("tenmincost", type=click.INT, required=True)
def decidefee(txsize, tenmincost, waitcostfn):
    """Compute optimal fee.

    txsize is transaction size in bytes.
    tenmincost is the cost in satoshis of waiting for the first ten minutes.
    waitcostfn (default 'quadratic') is the type of wait cost fn: linear
    or quadratic.
    """
    from tabulate import tabulate
    try:
        res = client.decidefee(txsize, tenmincost, waitcostfn)
    except Exception as e:
        click.echo(repr(e))
        return

    table = res.items()
    click.echo(tabulate(table))


@cli.command()
def mempool():
    '''Get mempool stats.'''
    from tabulate import tabulate
    try:
        stats = client.get_mempool()
    except Exception as e:
        click.echo(repr(e))
        return

    params = stats['params']
    table = [(paramname, param) for paramname, param in params.items()]
    click.echo("Params:")
    click.echo(tabulate(table))

    if 'feerates' not in stats:
        return

    headers = ['Feerate', 'Size (bytes)']
    table = zip(stats['feerates'], stats['cumsize'])
    click.echo("")
    click.echo('Cumul. Mempool Size')
    click.echo("=========================")
    click.echo(tabulate(table, headers=headers))
    click.echo('')

    table = [
        ("Current block height", stats['currheight']),
        ("Num txs", stats['numtxs']),
        ("Size with fee", stats['sizewithfee']),
        ("Num memblocks", stats['num_memblocks']),
    ]
    click.echo(tabulate(table))


@cli.command()
@click.argument('level', type=click.STRING, required=True)
def setloglevel(level):
    '''Set log level.

    level must be in ['debug', 'info', 'warning', 'error'].
    '''
    try:
        res = client.set_loglevel(level)
    except Exception as e:
        click.echo(repr(e))
    else:
        click.echo(res)
