#!/usr/bin/env python

from bartendro import app
import logging
import logging.handlers
import os
import memcache
import sys
from bartendro.global_lock import BartendroGlobalLock
from bartendro.router import driver
from bartendro import mixer
from bartendro.errors import SerialIOError, I2CIOError
from bartendro.options import load_options
import argparse

LOG_SIZE = 1024 * 500  # 500k maximum log file size
LOG_FILES_SAVED = 3    # number of log files to compress and save


parser = argparse.ArgumentParser(description='Bartendro application process')
parser.add_argument("-d", "--debug", help="Turn on debugging mode to see stack traces in the error log", default=True, action='store_true')
parser.add_argument("-t", "--host", help="Which interfaces to listen on. Default: 127.0.0.1", default="127.0.0.1", type=str)
parser.add_argument("-p", "--port", help="Which port to listen on. Default: 8080", default="8080", type=int)
parser.add_argument("-s", "--software-only", help="Run only the server software, without hardware interaction.", default=False, action='store_true')

args = parser.parse_args()

try:
    import uwsgi
    have_uwsgi = True
except ImportError:
    have_uwsgi = False

def print_software_only_notice():
    print """If you're trying to run this code without having Bartendro hardware,
you can still run the software portion of it in a simulation mode. In this mode no 
communication with the Bartendro hardware will happen to allow the software to run.
To enable this mode, set the BARTENDRO_SOFTWARE_ONLY environment variable to 1 and 
try again:

    > export BARTENDRO_SOFTWARE_ONLY=1

"""

# Set up logging
if not os.path.exists("logs"):
    os.mkdir("logs")

handler = logging.handlers.RotatingFileHandler(os.path.join("logs", "bartendro.log"), 
                                               maxBytes=LOG_SIZE, 
                                               backupCount=LOG_FILES_SAVED)
logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)
logger = logging.getLogger('bartendro')
logger.addHandler(handler)

app.options = load_options()

try: 
    app.software_only = args.software_only or int(os.environ['BARTENDRO_SOFTWARE_ONLY'])
    app.num_dispensers = 7
except KeyError:
    app.software_only = 0

if not os.path.exists("bartendro.db"):
    print "bartendro.db file not found. Please copy bartendro.db.default to "
    print "bartendro.db in order to provide Bartendro with a starting database."
    sys.exit(-1)

# Create a memcache connection and flush everything
app.mc = memcache.Client(['127.0.0.1:11211'], debug=0)
app.mc.flush_all()

# Create the Bartendro globals to prevent multiple people from using it at the same time.
app.globals = BartendroGlobalLock()

# Start the driver, which talks to the hardware
try:
    app.driver = driver.RouterDriver("/dev/ttyAMA0", app.software_only);
    app.driver.open()
except I2CIOError:
    print
    print "Cannot open I2C interface to a router board."
    print
    print_software_only_notice()
    sys.exit(-1)
except SerialIOError:
    print
    print "Cannot open serial interface to a router board."
    print
    print_software_only_notice()
    sys.exit(-1)

logging.info("Found %d dispensers." % app.driver.count())

if app.driver.count() == 1:
    app.options.use_shotbot_ui = True

app.mixer = mixer.Mixer(app.driver, app.mc)
if app.software_only:
    logging.info("Running SOFTWARE ONLY VERSION. No communication between software and hardware chain will happen!")

logging.info("Bartendro starting")
app.debug = args.debug

if __name__ == '__main__':
    app.run(host=args.host, port=args.port)
