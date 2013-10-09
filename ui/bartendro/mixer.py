# -*- coding: utf-8 -*-
import logging
from time import sleep, time
from threading import Thread
from flask import Flask, current_app
from flask.ext.sqlalchemy import SQLAlchemy
import memcache
from sqlalchemy.orm import mapper, relationship, backref
from bartendro import db, app
from bartendro.global_lock import STATE_INIT, STATE_READY, STATE_LOW, STATE_OUT, STATE_ERROR
from bartendro.model.drink import Drink
from bartendro.model.dispenser import Dispenser
from bartendro.model import drink_booze
from bartendro.model import booze
from bartendro.model import drink_log
from bartendro.model import shot_log

TICKS_PER_ML = 2.78
CALIBRATE_ML = 60 
CALIBRATION_TICKS = TICKS_PER_ML * CALIBRATE_ML

LIQUID_OUT_THRESHOLD       = 75
LIQUID_WARNING_THRESHOLD   = 120 

DISPENSER_OUT     = 1
DISPENSER_OK      = 0
DISPENSER_WARNING = 2

CLEAN_CYCLE_MAX_PUMPS = 5   # The maximum number of pups to run at any one time
CLEAN_CYCLE_DURATION  = 30  # in seconds for each pump

log = logging.getLogger('bartendro')

class BartendroBusyError(Exception):
    pass

class Mixer(object):
    '''This is where the magic happens!'''

    def __init__(self, driver, mc):
        self.driver = driver
        self.mc = mc
        self.err = ""
        self.disp_count = self.driver.count()
        self.check_liquid_levels()

    def reset(self):
        self.set_state(STATE_INIT)
        self.check_liquid_levels()

    def lock_bartendro(self):
        return app.globals.lock_bartendro()

    def unlock_bartendro(self):
        return app.globals.unlock_bartendro()

    def get_state(self):
        return app.globals.get_state()

    def set_state(self, state):
        return app.globals.set_state(state)

    def led_idle(self):
        self.driver.led_idle()

    def led_dispense(self):
        self.driver.led_dispense()

    def led_complete(self):
        self.driver.led_complete()

    def led_clean(self):
        self.driver.led_clean()

    def can_make_drink(self, boozes, booze_dict):
        
        ok = True
        for booze in boozes:
            try:
                foo = booze_dict[booze]
            except KeyError:
                ok = False
        return ok

    def check_liquid_levels(self):
        if self.get_state() == STATE_ERROR:
            return 

        if not app.options.use_liquid_level_sensors: 
            self.driver.set_status_color(0, 1, 0)
            self.set_state(STATE_READY)
            return

        new_state = STATE_READY

        # step 1: ask the dispensers to update their liquid levels
        if not self.driver.update_liquid_levels():
            log.error("Failed to update liquid levels")
            self.set_state(STATE_ERROR)
            return

        # wait for the dispensers to determine the levels
        sleep(.01)

        # Now ask each dispenser for the actual level
        dispensers = db.session.query(Dispenser).order_by(Dispenser.id).all()
        for i, dispenser in enumerate(dispensers):
            if i >= self.disp_count: break

            dispenser.out = DISPENSER_OK
            level = self.driver.get_liquid_level(i)
            if level < 0:
                log.error("Failed to read liquid levels from dispenser %d" % (i+1))
                return

            if level <= LIQUID_WARNING_THRESHOLD:
                if new_state == STATE_READY:
                    new_state = STATE_LOW
                if dispenser.out != DISPENSER_WARNING:
                    dispenser.out = DISPENSER_WARNING

            if level <= LIQUID_OUT_THRESHOLD:
                if new_state == STATE_READY or new_state == STATE_LOW:
                    new_state = STATE_OUT
                if dispenser.out != DISPENSER_OUT:
                    dispenser.out = DISPENSER_OUT

        db.session.commit()

        self.set_state(new_state)
        self.update_status_led()
        log.info("Checking levels done")

        return new_state

    def update_status_led(self):
        state = self.get_state()
        if state == STATE_OUT:
            self.driver.set_status_color(1, 0, 0)
        elif state == STATE_LOW:
            self.driver.set_status_color(1, 1, 0)
        elif state == STATE_READY:
            self.driver.set_status_color(0, 1, 0)
        else:
            self.driver.set_status_color(1, 1, 1)

    def liquid_level_test(self, dispenser, threshold):
        if self.get_state() == STATE_ERROR:
            return 
        if not app.options.use_liquid_level_sensors: return

        log.info("Start liquid level test: (disp %s thres: %d)" % (dispenser, threshold))

        if not self.driver.update_liquid_levels():
            log.error("Failed to update liquid levels")
            return
        sleep(.01)

        level = self.driver.get_liquid_level(dispenser)
	log.info("initial reading: %d" % level)
        if level <= threshold:
	    log.info("liquid is out before starting: %d" % level)
	    return

        last = -1
        self.driver.start(dispenser)
        while level > threshold:
            if not self.driver.update_liquid_levels():
                log.error("Failed to update liquid levels")
                return
            sleep(.01)
            level = self.driver.get_liquid_level(dispenser)
            if level != last:
                 log.info("  %d" % level)
            last = level

        self.driver.stop(dispenser)
        log.info("Stopped at level: %d" % level)
        sleep(.1);
        level = self.driver.get_liquid_level(dispenser)
        log.info("motor stopped at level: %d" % level)

    def get_available_drink_list(self):
        if self.get_state() == STATE_ERROR:
            return []

        can_make = self.mc.get("available_drink_list")
        if can_make: 
            return can_make

        add_boozes = db.session.query("abstract_booze_id") \
                            .from_statement("""SELECT bg.abstract_booze_id 
                                                 FROM booze_group bg 
                                                WHERE id 
                                                   IN (SELECT distinct(bgb.booze_group_id) 
                                                         FROM booze_group_booze bgb, dispenser 
                                                        WHERE bgb.booze_id = dispenser.booze_id)""")

        offline_boozes = db.session.query("id") \
                                .from_statement("""SELECT bg.id FROM booze bg WHERE bg.offline > 0""")

        if app.options.use_liquid_level_sensors: 
            sql = "SELECT booze_id FROM dispenser WHERE out == 0 ORDER BY id LIMIT :d"
        else:
            sql = "SELECT booze_id FROM dispenser ORDER BY id LIMIT :d"

        boozes = db.session.query("booze_id") \
                        .from_statement(sql) \
                        .params(d=self.disp_count).all()
        boozes.extend(add_boozes)
        boozes.extend(offline_boozes)
        
        booze_dict = {}
        for booze_id in boozes:
            booze_dict[booze_id[0]] = 1

        drinks = db.session.query("drink_id", "booze_id") \
                        .from_statement("SELECT d.id AS drink_id, db.booze_id AS booze_id FROM drink d, drink_booze db WHERE db.drink_id = d.id ORDER BY d.id, db.booze_id") \
                        .all()
        last_drink = -1
        boozes = []
        can_make = []
        for drink_id, booze_id in drinks:
            if last_drink < 0: last_drink = drink_id
            if drink_id != last_drink:
                if self.can_make_drink(boozes, booze_dict): 
                    can_make.append(last_drink)
                boozes = []
            boozes.append(booze_id)
            last_drink = drink_id

        if self.can_make_drink(boozes, booze_dict): 
            can_make.append(last_drink)

        self.mc.set("available_drink_list", can_make)
        return can_make

    def wait_til_finished_dispensing(self, disp):
        """Check to see if the given dispenser is still dispensing. Returns True when finished. False if over current"""
        timeout_count = 0
        while True:
            (is_dispensing, over_current) = app.driver.is_dispensing(disp)
            if is_dispensing < 0 or over_current < 0:
                continue

            log.debug("is_disp %d, over_cur %d" % (is_dispensing, over_current))
            if over_current: return False
            if is_dispensing == 0: return True

            # This timeout count is here to counteract Issue #64 -- this can be removed once #64 is fixed
            if is_dispensing == -1:
                timeout_count += 1
                if timeout_count == 3:
                    break

            sleep(.1)

    def dispense_ml(self, disp, ml, booze_id = -1):
        if disp < 0 or disp >= self.driver.count():
            return (0, "invalid dispenser")

        if self.get_state() == STATE_ERROR:
            return (0, "Bartendro is in error state")

        locked = self.lock_bartendro()
        if not locked: raise BartendroBusyError

        self.led_dispense()
        self.driver.dispense_ticks(disp, ml * TICKS_PER_ML)
        if not self.wait_til_finished_dispensing(disp):
            self.set_state(STATE_ERROR)
            self.update_status_led()
            self.unlock_bartendro()
            return (1, "Dispenser is current limited")
        self.led_idle()

        # If we're given a booze_id, log this shot
        if booze_id >= 0:
            t = int(time())
            slog = shot_log.ShotLog(booze_id, t, ml)
            db.session.add(slog)
            db.session.commit()

        self.unlock_bartendro()
        return (0, "")

    def find_dispenser(self, booze_id ):
        # Find the dispenser for this booze
        dispensers = Dispenser.query.order_by(Dispenser.id).all()

        for i in xrange(self.disp_count):
            disp = dispensers[i]

            # if we're out of booze, don't consider this drink
            if app.options.use_liquid_level_sensors and disp.out: 
                log.info("Dispenser %d is out of booze. Cannot make this drink." % (i+1))
                continue

            if booze_id == disp.booze_id:
                return disp
        
        return None
        
    def get_booze_name( self, booze_id ):
        drinks = db.session.query( "name") \
                        .from_statement("SELECT db.name FROM booze db WHERE db.id = :id").params( id = booze_id ).all()
                        
        print( "get_booze_name(%d, %s)" % ( booze_id, drinks ) )
        
        return drinks[0]

    def get_drink_name( self, drink_id ):
            
        return Drink.query.filter_by(id=int(drink_id)).first()
            

    def get_ingredients_for_drink( self, id, recipe_arg ):
        results = [ ]
        
        print "show_ingredients, id=%d args=%s" % ( id, recipe_arg )
        
        drink = Drink.query.filter_by(id=int(id)).first()
        print( "recipe_arg = %s" % str( recipe_arg ) )

        for booze in recipe_arg:
            booze_id = int(booze[5:])
        
            r = {}
                                    
            r['booze'] = booze_id
            r['ml'] = recipe_arg[booze]
            r['name'] = self.get_booze_name( booze_id )

            results.append( r )

        print "get_ingredients, results=%s for recipe=%s" % ( results, recipe_arg )

        return results
        

    def make_drink(self, id, recipe_arg, speed = 255):
        log.debug("Make drink state: %d id=%d recipe_arg=%s speed=%d" % ( self.get_state(), id, recipe_arg, speed ) )
        print "Make drink state: %d id=%d recipe_arg=%s speed=%d" % ( self.get_state(), id, recipe_arg, speed )
        if self.get_state() == STATE_ERROR:
            return "Cannot make a drink. Bartendro has encountered some error and is stopped. :("
        log.info("State ok! making drink!")

        # start by updating liqid levels to make sure we have the right fluids
        self.check_liquid_levels()

        drink = Drink.query.filter_by(id=int(id)).first()

        print( "recipe_arg = %s" % str( recipe_arg ) )
        
        recipe = []
        size = 0
                
        for booze in recipe_arg:
            booze_id = int(booze[5:])
            
            r = {}
            
            r['booze'] = booze_id
            r['ml'] = recipe_arg[booze]
            r['name'] = self.get_booze_name( booze_id )
            size += r['ml']
            
            # Find the dispenser for this booze
            disp = self.find_dispenser( booze_id )
            
            if disp != None:
                r['dispenser'] = disp.id
                r['dispenser_actual'] = disp.actual
            else:
                r['offline'] = 1
                
            recipe.append( r )
                                
        locked = self.lock_bartendro()
        if not locked: raise BartendroBusyError
   
        # Calculate the 'offline' ingredients
        log.debug("recipe = %s" % str( recipe ) )
        print( "recipe = %s" % str( recipe ) )
        
        offlineIngredients = []
        for r in recipe:
            if not 'dispenser' in r:
                offlineIngredients.append( r )
        
        print( "OFFLINE ingredients: %s" % str( offlineIngredients ) )
        
        #   THIS SHOULD PUT UP A DIALOG DESCRIBING THE NECESSARY INGREDIENTS
        
        
        #   DISPENSE THE INGREDIENTS
        self.led_dispense()
        dur = 0
        active_disp = []
        ticks = []
        for r in recipe:
            if not 'dispenser' in r:
                continue
            
            if r['dispenser_actual'] == 0:
                r['ms'] = int(r['ml'] * TICKS_PER_ML)
            else:
                r['ms'] = int(r['ml'] * TICKS_PER_ML * (CALIBRATE_ML / float(r['dispenser_actual'])))
            if not self.driver.dispense_ticks(r['dispenser'] - 1, int(r['ms']), speed):
                log.error("dispense_ticks: failed")
            ticks.append("disp %d for %s ticks" % (r['dispenser'] - 1, int(r['ms'])))
            active_disp.append(r['dispenser'])
            sleep(.01)

            if r['ms'] > dur: dur = r['ms']

        log.info("Making drink: %.2f ml of %s (%s)" % (size, drink.name.name, ", ".join(ticks)))

        current_sense = False
        for disp in active_disp:
            if not self.wait_til_finished_dispensing(disp-1):
                current_sense = True
                break

        if current_sense: 
            self.set_state(STATE_ERROR)
            self.update_status_led()
            self.unlock_bartendro()
            log.error("Current sense triggered on dispenser %d" % disp)
            return "One of the pumps did not operate properly. Your drink is broken. Sorry. :("

        self.led_complete()
        t = int(time())

        # Now, put up a window showing any additional steps that are needed to make this drink
        if len(offlineIngredients) > 0:
            print "There are offline ingredients %s" % offlineIngredients

            newURL = "/ws/showingredients/" + str(id) + "?" + '&'.join( [ "booze%d=%d" % ( i[ 'booze' ], i[ 'ml' ] ) for i in offlineIngredients ] )
            print "n=%s" % newURL

        dlog = drink_log.DrinkLog(drink.id, t, size)
        db.session.add(dlog)
        db.session.commit()


        


        if app.options.use_liquid_level_sensors:
            self.check_liquid_levels()

        FlashGreenLeds(self).start()
        self.unlock_bartendro()

        return "" 

    def clean(self):
        CleanCycle(self).start()

class CleanCycle(Thread):
    def __init__(self, mixer):
        Thread.__init__(self)
        self.mixer = mixer

    def run(self):
        disp_on_times = []
        disp_off_times = []
        for i in xrange(self.mixer.disp_count):
            disp_on_times.append(((i / CLEAN_CYCLE_MAX_PUMPS) * CLEAN_CYCLE_DURATION) + (i % CLEAN_CYCLE_MAX_PUMPS))
            disp_off_times.append(disp_on_times[-1] + CLEAN_CYCLE_DURATION)

        self.mixer.led_clean()
        for t in xrange(disp_off_times[-1] + 1):
            for i, off in enumerate(disp_off_times):
                if t == off: 
                    self.mixer.driver.stop(i)
            for i, on in enumerate(disp_on_times):
                if t == on: 
                    self.mixer.driver.start(i)
            sleep(1)
        self.mixer.led_idle()

        for i in xrange(self.mixer.disp_count):
            (is_dispensing, over_current) = app.driver.is_dispensing(i)
            if over_current:
                app.mixer.set_state(STATE_ERROR)
                app.mixer.update_status_led()
                break

class FlashGreenLeds(Thread):
    def __init__(self, mixer):
        Thread.__init__(self)
        self.mixer = mixer

    def run(self):
        sleep(5);
        self.mixer.led_idle()
