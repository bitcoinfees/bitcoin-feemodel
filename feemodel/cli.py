from __future__ import division

import click
from feemodel.apiclient import client


@click.group()
@click.option('--port', type=click.INT, default=None)
def cli(port):
    if port is not None:
        from feemodel.config import config
        config.set("app", "port", value=str(port))
        client.port = port


@cli.command()
@click.option('--mempool', is_flag=True,
              help='Collect memblock data only (no simulation)')
@click.option('--external', is_flag=True,
              help='Make the (Flask) server externally visible.')
def start(mempool, external):
    '''Start the simulation app.

    Use --mempool for memblock data collection only (no simulation).
    '''
    # TODO: add "raw" output option for all commands
    from feemodel.app.main import main, logfile
    from feemodel.config import __version__, pkgname, config
    click.echo("{} {}".format(pkgname, __version__))
    if mempool:
        click.echo("Starting mempool data collection; logging to %s"
                   % logfile)
    else:
        click.echo("Starting simulation app; logging to %s" % logfile)
    if external:
        config.set("app", "external", "true")
    main(mempool_only=mempool)


@cli.command()
def pools():
    '''Get mining pool statistics.'''
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
    if 'blockinterval' not in stats:
        # No valid estimates ready.
        click.echo("Insufficient number of blocks.")
        return

    table = zip(stats['feerates'], stats['caps'])

    headers = ['Feerate', 'Capacity (bytes/s)']
    click.echo("Cumul. Capacity")
    click.echo("===============================")
    click.echo(tabulate(table, headers=headers))
    click.echo("")
    click.echo("Block interval: {}s".format(stats['blockinterval']))


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

    if 'cumsize' not in stats:
        return

    headers = ['Feerate', 'Size (bytes)']
    table = zip(stats['cumsize']['feerates'], stats['cumsize']['size'])
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
