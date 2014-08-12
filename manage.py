#!/usr/bin/env python2.7
"""Main entry-point into the 'PyPi Portal' Flask and Celery application.

This is a demo Flask application used to show how I structure my large Flask
applications.

License: MIT
Website: https://github.com/Robpol86/Flask-Large-Application-Example

Command details:
    devserver           Run the application using the Flask Development
                        Server. Auto-reloads files when they change.
    tornadoserver       Run the application with Facebook's Tornado web
                        server. Forks into multiple processes to handle
                        several requests.
    celerydev           Starts a Celery worker with Celery Beat in the same
                        process.
    celerybeat          Run a Celery Beat periodic task scheduler.
    celeryworker        Run a Celery worker process.
    shell               Starts a Python interactive shell with the Flask
                        application context.
    create_all          Only create database tables if they don't exist and
                        then exit.

Usage:
    manage.py devserver [-p NUM] [-l DIR] [--config_prod]
    manage.py tornadoserver [-p NUM] [-l DIR] [--config_prod]
    manage.py celerydev [-l DIR] [--config_prod]
    manage.py celerybeat [-l DIR] [--config_prod]
    manage.py celeryworker [-l DIR] [--config_prod]
    manage.py shell [--config_prod]
    manage.py create_all [--config_prod]
    manage.py (-h | --help)

Options:
    --config_prod           Load the production configuration instead of
                            development.
    -l DIR --log_dir=DIR    Log all statements to file in this directory
                            instead of stdout.
                            Only ERROR statements will go to stdout. stderr is
                            not used.
    -p NUM --port=NUM       Flask will listen on this port number.
                            [default: 5000]
"""

from __future__ import print_function
from functools import wraps
import logging
import os
import signal
import sys

from celery.app.log import Logging
from celery.bin.celery import main as celery_main
from docopt import docopt
import flask
from flask.ext.script import Shell
from tornado import httpserver, ioloop, web, wsgi

from pypi_portal.application import create_app, get_config
from pypi_portal.extensions import db

OPTIONS = docopt(__doc__)


class CustomFormatter(logging.Formatter):
    LEVEL_MAP = {logging.FATAL: 'F', logging.ERROR: 'E', logging.WARN: 'W', logging.INFO: 'I', logging.DEBUG: 'D'}

    def format(self, record):
        record.levelletter = self.LEVEL_MAP[record.levelno]
        return super(CustomFormatter, self).format(record)


def setup_logging():
    """Setup Google-Style logging for the entire application.

    At first I hated this but I had to use it for work, and now I prefer it. Who knew?
    From: https://github.com/twitter/commons/blob/master/src/python/twitter/common/log/formatters/glog.py

    Always logs DEBUG statements somewhere.
    """
    log_to_disk = False
    if OPTIONS['--log_dir']:
        if not os.path.isdir(OPTIONS['--log_dir']):
            print('ERROR: Directory {} does not exist.'.format(OPTIONS['--log_dir']))
            sys.exit(1)
        if not os.access(OPTIONS['--log_dir'], os.W_OK):
            print('ERROR: No permissions to write to directory {}.'.format(OPTIONS['--log_dir']))
            sys.exit(1)
        log_to_disk = True

    fmt = '%(levelletter)s%(asctime)s.%(msecs)d %(process)d %(filename)s:%(lineno)d] %(message)s'
    datefmt = '%m%d %H:%M:%S'
    formatter = CustomFormatter(fmt, datefmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.ERROR if log_to_disk else logging.DEBUG)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console_handler)


def log_messages(app, port, fsh_folder):
    """Log messages common to Tornado and devserver."""
    log = logging.getLogger(__name__)
    log.info('Server is running at http://0.0.0.0:{}/'.format(port))
    log.info('Flask version: {}'.format(flask.__version__))
    log.info('DEBUG: {}'.format(app.config['DEBUG']))
    log.info('FLASK_STATICS_HELPER_FOLDER: {}'.format(fsh_folder))
    log.info('STATIC_FOLDER: {}'.format(app.static_folder))


def parse_options():
    """Parses command line options for Flask.

    Returns:
    Config instance to pass into create_app().
    """
    # Figure out which class will be imported.
    if OPTIONS['--config_prod']:
        config_class_string = 'pypi_portal.config.Production'
    else:
        config_class_string = 'pypi_portal.config.Config'
    config_obj = get_config(config_class_string)

    return config_obj


def command(func):
    """Decorator that registers the chosen command/function.

    If a function is decorated with @command but that function name is not a valid "command" according to the docstring,
    a KeyError will be raised, since that's a bug in this script.

    If a user doesn't specify a valid command in their command line arguments, the above docopt(__doc__) line will print
    a short summary and call sys.exit() and stop up there.

    If a user specifies a valid command, but for some reason the developer did not register it, an AttributeError will
    raise, since it is a bug in this script.

    Finally, if a user specifies a valid command and it is registered with @command below, then that command is "chosen"
    by this decorator function, and set as the attribute `chosen`. It is then executed below in
    `if __name__ == '__main__':`.

    Doing this instead of using Flask-Script.

    Positional arguments:
    func -- the function to decorate
    """
    @wraps(func)
    def wrapped():
        return func()

    # Register chosen function.
    if func.__name__ not in OPTIONS:
        raise KeyError('Cannot register {}, not mentioned in docstring/docopt.'.format(func.__name__))
    if OPTIONS[func.__name__]:
        command.chosen = func

    return wrapped


@command
def devserver():
    app = create_app(parse_options())
    fsh_folder = app.blueprints['flask_statics_helper'].static_folder
    log_messages(app, OPTIONS['--port'], fsh_folder)
    app.run(host='0.0.0.0', port=int(OPTIONS['--port']))


@command
def tornadoserver():
    app = create_app(parse_options())
    fsh_folder = app.blueprints['flask_statics_helper'].static_folder
    log_messages(app, OPTIONS['--port'], fsh_folder)

    # Setup the application.
    container = wsgi.WSGIContainer(app)
    application = web.Application([
        (r'/static/flask_statics_helper/(.*)', web.StaticFileHandler, dict(path=fsh_folder)),
        (r'/(favicon\.ico)', web.StaticFileHandler, dict(path=app.static_folder)),
        (r'/static/(.*)', web.StaticFileHandler, dict(path=app.static_folder)),
        (r'.*', web.FallbackHandler, dict(fallback=container))
    ])  # From http://maxburstein.com/blog/django-static-files-heroku/
    http_server = httpserver.HTTPServer(application)
    http_server.bind(OPTIONS['--port'])

    # Start the server.
    http_server.start(0)  # Forks multiple sub-processes
    ioloop.IOLoop.instance().start()


@command
def celerydev():
    app = create_app(parse_options(), no_sql=True)
    Logging._setup = True  # Disable Celery from setting up logging, already done in setup_logging().
    celery_args = ['celery', 'worker', '-B', '-s', '/tmp/celery.db', '--concurrency=5']
    with app.app_context():
        return celery_main(celery_args)


@command
def celerybeat():
    app = create_app(parse_options(), no_sql=True)
    Logging._setup = True
    celery_args = ['celery', 'beat', '-C', '--pidfile=celery_beat.pid']
    with app.app_context():
        return celery_main(celery_args)


@command
def celeryworker():
    app = create_app(parse_options(), no_sql=True)
    Logging._setup = True
    celery_args = ['celery', 'worker', '-C', '--autoscale=10,1', '--without-gossip']
    with app.app_context():
        return celery_main(celery_args)


@command
def shell():
    app = create_app(parse_options())
    app.app_context().push()
    Shell(make_context=lambda: dict(app=app, db=db)).run(no_ipython=False, no_bpython=False)


@command
def create_all():
    app = create_app(parse_options())
    with app.app_context():
        db.create_all()


if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))  # Properly handle Control+C
    setup_logging()
    if not OPTIONS['--port'].isdigit():
        print('ERROR: Port should be a number.')
        sys.exit(1)
    getattr(command, 'chosen')()  # Execute the function specified by the user.
