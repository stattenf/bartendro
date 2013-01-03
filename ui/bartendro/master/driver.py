#!/usr/bin/env python

import os
from subprocess import call
from time import sleep, localtime, time
import smbus
import serial
import random
from struct import pack
import pack7

BAUD_RATE = 38400

MAX_DISPENSERS = 15
SHOT_TICKS     = 20

PACKET_ACK_OK      = 0
PACKET_CRC_FAIL    = 1
PACKET_ACK_TIMEOUT = 2

PACKET_PING            = 3
PACKET_SET_MOTOR_SPEED = 4
PACKET_TICK_DISPENSE   = 5
PACKET_TIME_DISPENSE   = 6
PACKET_LED_OFF         = 7
PACKET_LED_IDLE        = 8
PACKET_LED_DISPENSE    = 9
PACKET_LED_DRINK_DONE  = 10
PACKET_COMM_TEST       = 0xFE
PACKET_BROADCAST       = 0xFF

ROUTER_BUS              = 0
ROUTER_ADDRESS          = 4
ROUTER_SELECT_CMD_BEGIN = 0
ROUTER_SELECT_CMD_END   = MAX_DISPENSERS
ROUTER_CMD_SYNC_ON      = 251
ROUTER_CMD_SYNC_OFF     = 252
ROUTER_CMD_PING         = 253
ROUTER_CMD_COUNT        = 254
ROUTER_CMD_RESET        = 255

# TODO 
# NameError: global name 'I2CIOError' is not defined
# CHeck timeout ACK that comes in less than timeout time
# Improve error handling and repeating requests that failed
# Hook up more LED algs & drive during drink making

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

    def __init__(self, device, software_only):
        self.device = device
        self.ser = None
        self.msg = ""
        self.ret = 0
        self.num_dispensers = 2
        self.selected = 0
        self.cl = None; #open("logs/comm.log", "a")
        self.software_only = software_only
        self.router = None

    def log(self, msg):
        return
        if self.software_only: return
        try:
            t = localtime()
            self.cl.write("%d-%d-%d %d:%02d %s" % (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, msg))
            self.cl.flush()
        except IOError:
            pass

    def reset(self):
        if self.software_only: return
        self.router.write_byte(ROUTER_ADDRESS, ROUTER_CMD_RESET)

    def select(self, dispenser):
        if self.software_only: return
        if dispenser < self.num_dispensers and self.selected != dispenser:
            self.selected = dispenser
            self.router.write_byte(ROUTER_ADDRESS, dispenser)
            sleep(.01)

    def sync(self, state):
        if self.software_only: return
        if (state):
            self.router.write_byte(ROUTER_ADDRESS, ROUTER_CMD_SYNC_ON)
        else:
            self.router.write_byte(ROUTER_ADDRESS, ROUTER_CMD_SYNC_OFF)

    def count(self):
        return self.num_dispensers

    def open(self):
        '''Open the serial connection to the master'''

        if self.software_only: return

        try:
            print "Opening %s" % self.device
            self.ser = serial.Serial(self.device, 
                                     BAUD_RATE, 
                                     bytesize=serial.EIGHTBITS, 
                                     parity=serial.PARITY_NONE, 
                                     stopbits=serial.STOPBITS_ONE,
                                     timeout=2)
        except serial.serialutil.SerialException:
            raise SerialIOError

        self.log("Opened %s for %d baud N81" % (self.device, BAUD_RATE))

        try:
            self.router = smbus.SMBus(ROUTER_BUS)
        except IOError:
            raise I2CIOError

        self.reset();
        sleep(3);
#self.led_idle()

    def close(self):
        if self.software_only: return
        self.ser.close()
        self.bus.close()
        self.ser = None
        self.bus = None

    def crc16_update(self, crc, a):
        crc ^= a;
        for i in xrange(0, 8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001;
            else:
                crc = (crc >> 1);

        return crc;

    def send_packet(self, packet):
        if self.software_only: return True

        # If there are any spurious characters, nuke em!
        self.ser.flushInput()
        self.ser.flushOutput()

        # Write the header signature FF FF
        self.ser.write(chr(0xFF))
        self.ser.write(chr(0xFF))

        crc = 0
        for ch in packet:
            crc = self.crc16_update(crc, ord(ch))

        encoded = pack7.pack_7bit(packet + pack("<H", crc))
        self.ser.write(chr(len(encoded)))
        self.ser.write(encoded)

        t0 = time()
        ch = self.ser.read(1)
        t1 = time()
        print "packet time: %f" % (t1 - t0)
        if len(ch) < 1:
            print "  * read timeout"
            return False
        ack = ord(ch)
        if ack == PACKET_ACK_OK: return True
        if ack == PACKET_CRC_FAIL: 
            print "  * crc fail"
            return False
        if ack == PACKET_ACK_TIMEOUT: 
            print "  * ack timeout"
            return False

        print "  * Invalid ACK code %d" % ord(ch)
        return False

    def send_packet8(self, dest, type, val):
        return self.send_packet(pack("BBBBBB", dest, type, val, 0, 0, 0))

    def send_packet16(self, dest, type, val):
        return self.send_packet(pack("<BBHH", dest, type, val, 0))

    def send_packet32(self, dest, type, val):
        return self.send_packet(pack("<BBI", dest, type, val))

    def make_shot(self):
        self.send_packet32(0, 5, 80)
        return True

    def ping(self):
        return self.send_packet32(self.selected, PACKET_PING, 0)

    def start(self, dispenser):
        self.select(dispenser)
        return self.send_packet8(dispenser, PACKET_SET_MOTOR_SPEED, 255)

    def stop(self, dispenser):
        self.select(dispenser)
        return self.send_packet8(dispenser, PACKET_SET_MOTOR_SPEED, 0)

    def dispense_time(self, dispenser, duration):
        return True

    def dispense_ticks(self, dispenser, ticks):
        print "dispense %d ticks" % ticks
        self.select(dispenser)
        return self.send_packet32(dispenser, PACKET_TICK_DISPENSE, ticks)

    def led_off(self):
        # TODO: use broadcast
        self.sync(0)
        for dispenser in xrange(self.num_dispensers):
            self.select(dispenser)
            self.send_packet8(dispenser, PACKET_LED_OFF, 0);
        return True

    def led_idle(self):
        # TODO: use broadcast
        self.sync(0)
        for dispenser in xrange(self.num_dispensers):
            self.select(dispenser)
            self.send_packet8(dispenser, PACKET_LED_IDLE, 0);
        sleep(.01)
        self.sync(1)
        return True

    def led_dispense(self):
        # TODO: use broadcast
        self.sync(0)
        for dispenser in xrange(self.num_dispensers):
            self.select(dispenser)
            self.send_packet8(dispenser, PACKET_LED_DISPENSE, 0);
        sleep(.01)
        self.sync(1)
        return True

    def led_complete(self):
        # TODO: use broadcast
        self.sync(0)
        for dispenser in xrange(self.num_dispensers):
            self.select(dispenser)
            self.send_packet8(dispenser, PACKET_LED_DRINK_DONE, 0);
        sleep(.01)
        self.sync(1)
        return True

    def comm_test(self):
        self.sync(0)
        self.select(0)
        return self.send_packet8(0, PACKET_COMM_TEST, 0);

    def is_dispensing(self, dispenser):
        return False

    def get_liquid_level(self, dispenser):
        return 80

    def get_dispense_stats(self, dispenser):
        return (0, 0)

def ping_test(md):
    while True:
        md.select(0)
        while True:
            print "ping 0:"
            ret = md.ping()
            if ret: break
            print "re-transmit"
            sleep(1)
        sleep(1)

        print "ping 1:"
        md.select(1)
        while True:
            ret = md.ping()
            if ret: break
            print "re-transmit"
        sleep(1)

def led_test(md):
    while True:
        print "idle"
        md.led_idle()
        sleep(5)
        print "dispense"
        md.led_dispense()
        sleep(5)
        print "complete"
        md.led_complete()
        sleep(5)

def comm_test(md):
    print "put disp 0 into comm test"
    md.select(0)
    while not md.comm_test():
        sleep(1)

    print "put disp 1 into comm test"
    md.select(1)
    while not md.comm_test():
        sleep(1)

if __name__ == "__main__":
    md = MasterDriver("/dev/ttyAMA0", 0);
    md.open()
#    ping_test(md)
    led_test(md)
