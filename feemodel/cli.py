import click
from feemodel.config import app_port

base_url = 'http://localhost:' + str(app_port) + '/feemodel/'


def get_resource(path):
    import requests
    try:
        r = requests.get(base_url + path)
        stat = r.json()
    except Exception as e:
        click.echo("Error connecting to the app.")
        raise e
    else:
        if not r:
            click.echo("Invalid command.")
        elif not stat:
            click.echo("Stat not available at this time.")
        else:
            return stat
        raise SystemExit


@click.group()
def cli():
    pass


@cli.command()
@click.option('--mempool', is_flag=True,
              help='Collect mempool data only (no simulation)')
def start(mempool):
    '''Start the simulation app.
    Use --mempool for mempool data collection only (no simulation).
    '''
    from feemodel.app.main import main
    from feemodel.config import applogfile
    if mempool:
        click.echo("Starting mempool data collection; logging to %s"
                   % applogfile)
    else:
        click.echo("Starting simulation app; logging to %s" % applogfile)
    main(mempool_only=mempool)


@cli.command()
def status():
    '''Get the app status.

    [mempool]

        'running' if everything is OK, else 'stopped'. While running,
        mempool data at each block is collected and written to disk.

    [height]

        The current best height of the Bitcoin block chain.

    [runtime]

        Time in seconds that the app has been running.

    [numhistory]

        Number of MemBlocks that have been written and are available on disk.

    Only if --mempool was not used -

    [poolestimator, steadystate, transient]

        'running' if the stats are being computed, 'idle' if waiting for
        the next update period, 'stopped' if there's a problem (it's
        configured to auto-restart, though), or it's waiting for data
        (more blocks, or e.g. sim waiting on pool estimates).
    '''
    click.echo('')
    status = get_resource('status')
    baseorder = ['mempool', 'height', 'runtime', 'numhistory']
    for key in baseorder:
        click.echo("%s: %s" % (key, status[key]))

    simorder = ['poolestimator', 'steadystate', 'transient']
    try:
        for key in simorder:
            click.echo("%s: %s" % (key, status[key]))
    except KeyError:
        pass
    click.echo('')


# #@cli.command()
# #def predictscores():
# #    from tabulate import tabulate
# #    scores = get_resource("predictscores")
# #    headers = [
# #        'Feerate',
# #        '']
# #
# #
@cli.command()
def transient():
    '''Get transient simulation statistics.'''
    import time
    from tabulate import tabulate
    stats = get_resource('transient')
    headers = [
        'Feerate',
        'Avgwait',
        'Error',
        'Predict']
    table = zip(
        stats['feerates'],
        stats['avgwaits'],
        stats['avgwaits_errors'],
        stats['predictwaits'],)
    click.echo('\nTransient statistics\n=======================')
    click.echo(tabulate(table, headers=headers))

    headers = [
        'Feerate',
        'Tx byterate',
        'Cap (lower)',
        'Cap (upper)']
    table = zip(
        stats['cap']['feerates'],
        stats['cap']['tx_byterates'],
        stats['cap']['cap_lower'],
        stats['cap']['cap_upper'],)
    click.echo('\nCapacity\n========')
    click.echo(tabulate(table, headers=headers))

    click.echo('\nMisc Stats\n==========')
    click.echo('Stable feerate: %d' % stats['stablefeerate'])
    click.echo('Timestamp: %s' % time.ctime(stats['timestamp']))
    click.echo('Time spent: %s\n' % int(stats['timespent']))
    click.echo('Predict level: %.2f' % stats['predictlevel'])


@cli.command()
def steadystate():
    '''Get steady-state simulation statistics.'''
    # TODO: fill in the rest of the docstring/help
    import time
    from tabulate import tabulate
    stats = get_resource('steadystate')
    headers = [
        'Feerate',
        'Avgwait',
        'SP',
        'ASB']
    table = zip(
        stats['sim']['feerates'],
        stats['sim']['avgwaits'],
        stats['sim']['strandedprop'],
        stats['sim']['avg_strandedblocks'],)
    click.echo('\nSteady-state statistics\n=======================')
    click.echo(tabulate(table, headers=headers))

    headers = [
        'Feerate',
        'Avgwait',
        'Error']
    table = zip(
        stats['measured']['feerates'],
        stats['measured']['avgwaits'],
        stats['measured']['errors'],)
    click.echo('\nMeasured statistics\n===================')
    click.echo(tabulate(table, headers=headers))

    headers = [
        'Feerate',
        'Tx byterate',
        'Cap (lower)',
        'Cap (upper)']
    table = zip(
        stats['cap']['feerates'],
        stats['cap']['tx_byterates'],
        stats['cap']['cap_lower'],
        stats['cap']['cap_upper'],)
    click.echo('\nCapacity\n========')
    click.echo(tabulate(table, headers=headers))

    click.echo('\nMisc Stats\n==========')
    click.echo('Stable feerate: %d' % stats['stablefeerate'])
    click.echo('Timestamp: %s' % time.ctime(stats['timestamp']))
    click.echo('Time spent: %s\n' % int(stats['timespent']))


@cli.command()
def pools():
    '''Get mining pool statistics.

    *** COLUMNS ***

    [Name]

        Name of the pool, as specified by blockchain.info's pools.json.
        If pool is unidentified, the name is the first valid coinbase payout
        address. (https://github.com/blockchain/Blockchain-Known-Pools)

    [HR (Thps)]

        Hashrate in Terahashes per second.

    [Prop]

        Proportion of the total hashrate.

    [MBS]

        Max block size of the pool.

    [MFR]

        Min fee rate of the pool.

    [AKN]

        "Above k and n" values. The number of transactions (k) that were
        above the min fee rate and were included in their respective block,
        compared with the total number of transactions (n).

    [BKN]

        "Below k and n" values. Identical to AKN but for transactions below
        the min fee rate.

    The following cols relate to the sampling distribution of the MFR
    estimator, obtained using bootstrap resampling.

    [Mean]

        Expected value of the MFR estimator.

    [Std]

        Standard deviation of the MFR estimator.

    [Bias]

        Bias of the MFR estimator.

    *** MISC STATS ***

    [Timestamp]

        Date/time at which the estimates were computed, in local time.

    [Block interval]

        The estimated mean time between blocks.
    '''
    import time
    from tabulate import tabulate
    stats = get_resource('pools')
    pools = stats['pools']
    headers = [
        'Name',
        'HR (Thps)',
        'Prop',
        'MBS',
        'MFR',
        'AKN',
        'BKN',
        'Mean',
        'Std',
        'Bias'
    ]
    table = []
    pitems = sorted(pools.items(),
                    key=lambda p: p[1]['proportion'], reverse=True)
    for pool in pitems:
        row = [
            pool[0],
            '%0.f' % (pool[1]['hashrate']*1e-12),
            '%.4f' % pool[1]['proportion'],
            '%d' % pool[1]['maxblocksize'],
            '%.0f' % pool[1]['minfeerate'],
            pool[1]['abovekn'],
            pool[1]['belowkn'],
            '%.2f' % pool[1]['mean'],
            '%.2f' % pool[1]['std'],
            '%.2f' % pool[1]['bias'],
        ]
        table.append(row)

    click.echo('')
    click.echo(tabulate(table, headers=headers))
    click.echo("\nTimestamp: %s" % time.ctime(stats['timestamp']))
    click.echo("Block interval: %.2f\n" % stats['blockinterval'])
