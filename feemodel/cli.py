import click
from feemodel.apiclient import client


@click.group()
def cli():
    pass


@cli.command()
@click.option('--mempool', is_flag=True,
              help='Collect memblock data only (no simulation)')
def start(mempool):
    '''Start the simulation app.

    Use --mempool for memblock data collection only (no simulation).
    '''
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
    for pool in pitems:
        row = [
            pool[0],
            pool[1]['hashrate']*1e-12,
            pool[1]['proportion'],
            pool[1]['maxblocksize'],
            pool[1]['minfeerate'],
            pool[1]['abovekn'],
            pool[1]['belowkn'],
            pool[1]['mfrmean'],
            pool[1]['mfrstd'],
            pool[1]['mfrbias'],
        ]
        table.append(row)

    click.echo('')
    click.echo(tabulate(table, headers=headers))
    click.echo('')
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
        'Wait',
        'Error']
    table = zip(
        stats['feepoints'],
        stats['expectedwaits'],
        stats['expectedwaits_errors'],)
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

    headers = ['Feerate', 'Bytes/s']
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
@click.argument('conftime', type=click.INT, required=True)
def estimatefee(conftime):
    '''Feerate estimation.

    Estimate feerate (satoshis) to have an average wait / confirmation
    time of CONFTIME minutes.
    '''
    try:
        res = client.estimatefee(conftime)
    except Exception as e:
        click.echo(repr(e))
        return
    click.echo(res['feerate'])


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

    headers = ['Feerate', 'Size']
    table = zip(stats['feerates'], stats['cumsize'])
    click.echo("")
    click.echo('Cumul. Mempool Size')
    click.echo("===================")
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
