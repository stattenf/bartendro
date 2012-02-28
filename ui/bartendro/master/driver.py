#!/usr/bin/env python

import os
from bartendro.utils import log, error
from subprocess import call
from gpio import GPIO
from time import sleep
import serial

BAUD_RATE = 38400

MAX_DISPENSERS = 15

class SttyNotFoundException:
    pass

class SerialPortException:
    pass

class SerialIOError:
    pass

class LogFileException:
    pass

class MasterDriver(object):
    '''This object interacts with the bartendro master controller.'''

    def __init__(self, device, logfile):
        self.device = device
        self.logfile = logfile
        self.ser = None
        self.msg = ""
        self.ret = 0
        self.ss = GPIO(135)
        self.ss.setup()
        self.num_dispensers = 0

    def open(self):
        '''Open the serial connection to the master'''

        try: 
            self.software_only = int(os.environ['BARTENDRO_SOFTWARE_ONLY'])
            self.num_dispensers = 15
        except KeyError:
            self.software_only = 0

        if self.software_only:
            log("Running SOFTWARE ONLY VERSION. No communication between software and hardware chain will happen!")
            return

        try:
            self.ser = serial.Serial(self.device, 
                                     BAUD_RATE, 
                                     bytesize=serial.EIGHTBITS, 
                                     parity=serial.PARITY_NONE, 
                                     stopbits=serial.STOPBITS_ONE, 
                                     timeout=2)
            self.l = open(self.logfile, "a")
        except serial.serialutil.SerialException:
            raise SerialIOError;

        log("Opened %s for %d baud N81" % (self.device, BAUD_RATE))

    def chain_init(self):

        if self.software_only: return

        log("initialize communication chain")

        # reset the chain
        print "send reset"
        self.ss.low()
        sleep(1)

        self.ss.high()
        sleep(.2)
        self.ss.low()
        sleep(.2)

        print "address assignment"
        while True:
            self.ser.write("0\n")
            r = self.ser.readline()
            if len(r) == 0:
                continue
            break

        if len(r) > 0:
            r= r[0:-1]
	    print "received '%s'" % r
            num = int(r)
            if num < 1 or num > MAX_DISPENSERS:
		error("Found an invalid number of dispensers. Communication chain busted!")
                self.num_dispensers = -1;
            else: 
		log("found %d dispensers" % int(r))
		self.num_dispensers = int(r)
                sleep(1)
        else:
            error("Cannot communicate with dispenser chain!")

    def close(self):
        if self.software_only: return
        self.ser.close()
        self.ser = None

    def send(self, cmd):
        if self.software_only: return
        self.ser.write(cmd)
        ret = self.ser.readline()
        if ret == "": 
            error("Serial comms timeout after cmd '%s'." % cmd[0:len(cmd)-1])
        return ret

    def count(self):
        return self.num_dispensers

    def start(self, dispenser):
        return self.send("%d on\n" % dispenser)

    def stop(self, dispenser):
        return self.send("%d off\n" % dispenser)

    def dispense(self, dispenser, duration):
        return self.send("%d disp %d\n" % (dispenser, duration))

    def led(self, dispenser, r, g, b):
        return self.send("%d led %d %d %d\n" % (dispenser, r, g, b))

    def is_dispensing(self, dispenser):
        '''expects "!3 isdisp 1" '''

        if self.software_only: return False

        self.send("%d isdisp\n" % dispenser)
        ret = self.ser.readline()
        if not ret: 
	    return False
        try:
            disp, cmd, value = ret.split(" ")
	    if value[0] == '1': 
	        return True
	    else:
	        return False
        except ValueError:
	    return False

    def ping(self, dispenser):
        '''expects "!3 pong" '''
        if self.software_only: return True

        self.send("%d ping\n" % dispenser)
        ret = self.ser.readline()
        if not ret: 
            error("ping response timeout")
	    return False

 	ret = ret[:-1]
        try:
            ret = ret[1:] # strip off the !
            disp, cmd = ret.split(" ")
 	    disp = int(disp)
	    if disp == dispenser: 
	        return True
	    else:
                error("wrong dispenser number in pong")
	        return False
        except ValueError:
            error("error parsing pong data. response: '%s'" % ret)
	    return False

if __name__ == "__main__":
    md = MasterDriver("/dev/ttyS1", "log");
    md.open()
    md.chain_init()
    sleep(1)
    md.dispense(0, 3000);
    while md.is_dispensing(0):
        sleep(.1)
