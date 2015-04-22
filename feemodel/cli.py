import click
from feemodel.apiclient import APIClient

client = APIClient()


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
            pool[1]['mfrstd'],
        ]
        table.append(row)

    click.echo('')
    click.echo(tabulate(table, headers=headers))
    click.echo('')
    click.echo("Total hashrate (Thps): {}".
               format(stats['totalhashrate']*1e-12))
    click.echo("Block interval (s): {}".format(stats['blockinterval']))
    click.echo("Timestamp: {}".format(time.ctime(stats['timestamp'])))
    click.echo("Next update: {}".format(time.ctime(stats['next_update'])))


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
        'Expected wait',
        'Error']
    table = zip(
        stats['feepoints'],
        stats['expectedwaits'],
        stats['expectedwaits_errors'],)
    click.echo('\nTransient statistics\n=======================')
    click.echo(tabulate(table, headers=headers))

    headers = [
        'Feerate',
        'Tx byterate',
        'Cap']
    table = zip(
        stats['cap']['feerates'],
        stats['cap']['txbyterates'],
        stats['cap']['caps'])
    click.echo('\nCapacity\n========')
    click.echo(tabulate(table, headers=headers))

    click.echo('')
    click.echo('Mempool size (bytes): {}'.format(stats['mempoolsize']))
    click.echo('Max cap (bytes/s): {}'.format(stats['cap']['maxcap']))
    click.echo('Stable feerate: {}'.format(stats['stablefeerate']))
    click.echo('Timestamp: {}'.format(time.ctime(stats['timestamp'])))
    click.echo('Time spent: {}'.format(stats['timespent']))
    click.echo('Num iters: {}'.format(stats['numiters']))


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
    click.echo("P-Value ECDF")
    click.echo("============")
    click.echo(tabulate(table, headers=headers))
    click.echo('')
    click.echo('p-distance: {}'.format(stats['pdistance']))
    click.echo('Num txs: {}'.format(stats['numtxs']))


@cli.command()
def txrate():
    """Get tx rate statistics."""
    from tabulate import tabulate
    try:
        stats = client.get_txrate()
    except Exception as e:
        click.echo(repr(e))
        return

    table = [(stat, name) for stat, name in stats.items()]
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

    headers = ['Feerate', 'Cumul Size (bytes)']
    table = zip(stats['feerates'], stats['cumsize'])
    click.echo('\nMempool size\n===============')
    click.echo(tabulate(table, headers=headers))

    click.echo('')
    click.echo("Current block height: {}".format(stats['currheight']))
    click.echo("Num memblocks: {}".format(stats['num_memblocks']))
    click.echo("Num txs: {}".format(stats['numtxs']))


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
