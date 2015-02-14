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
        return stat


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
        click.echo("Starting mempool data collection; logging to %s" % applogfile)
    else:
        click.echo("Starting simulation app; logging to %s" % applogfile)
    main(mempool_only=mempool)


@cli.command()
def status():
    '''Get the app status.

    mempool: 'running' if everything is OK, else 'stopped'. While running,
             mempool data at each block is collected and written to disk.

    height: The current best block height in the Bitcoin network.

    runtime: Time in seconds that the app has been running.

    numhistory: Number of MemBlocks that have been written and are available
                on disk.

    Only if --mempool was not used -

    poolestimator, steadystate, transient:

        'running' if the stats are being computed, 'idle' if waiting for
        the next update period, 'stopped' if there's a problem (it's
        configured to auto-restart, though)
    '''
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


@cli.command()
def pools():
    '''Get mining pool statistics.'''
    stats = get_resource('pools')
