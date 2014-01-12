# -*- coding: utf-8 -*-
import memcache
from sqlalchemy import func, asc
from sqlalchemy.exc import OperationalError
from bartendro import app, db
from bartendro.global_lock import STATE_ERROR
from flask import Flask, request, render_template
from bartendro.model.dispenser import Dispenser
from bartendro.model.drink import Drink
from bartendro.model.drink_name import DrinkName
from bartendro.model.booze import Booze


def process_ingredients(drinks):
    for drink in drinks:
        drink.process_ingredients()

def filter_drink_list(can_make_dict, drinks):
    filtered = []
    for drink in drinks:
        try:
            foo =can_make_dict[drink.id]
            filtered.append(drink)
        except KeyError:
            pass
    return filtered

@app.route('/')
def index():
    if app.options.use_shotbot_ui:
        return shotbot()

    if app.globals.get_state() == STATE_ERROR:
        return render_template("index", 
                               top_drinks=[], 
                               other_drinks=[],
                               error_message="Bartendro is in trouble!<br/><br/>I need some attention! Please find my master, so they can make me feel better.",
                               title="Bartendro error")

    try:
        can_make = app.mixer.get_available_drink_list()
    except OperationalError:
        return render_template("index", 
                               top_drinks=[], 
                               other_drinks=[],
                               drinks_1=app.mixer.get_available_drink_list(1),
                               drinks_2=app.mixer.get_available_drink_list(2),                               
                               error_message="Bartendro database errror.<br/><br/>There doesn't seem to be a valid database installed.",
                               title="Bartendro error")
        


    if not len(can_make):
        return render_template("index", 
                               top_drinks=[], 
                               other_drinks=[],
                               error_message="Drinks can't be made with the available boozes.<br/><br/>I need some attention! Please find my master, so they can make me feel better.",
                               title="Bartendro error")

    can_make_dict = {}
    for drink in can_make:
        can_make_dict[drink] = 1

    top_drinks = db.session.query(Drink) \
                        .join(DrinkName) \
                        .filter(Drink.name_id == DrinkName.id)  \
                        .filter(Drink.popular == 1)  \
                        .filter(Drink.available == 1)  \
                        .order_by(asc(func.lower(DrinkName.name))).all() 
          
    top_drinks = filter_drink_list(can_make_dict, top_drinks)
    process_ingredients(top_drinks)

    other_drinks = db.session.query(Drink) \
                        .join(DrinkName) \
                        .filter(Drink.name_id == DrinkName.id)  \
                        .filter(Drink.popular == 0)  \
                        .filter(Drink.available == 1)  \
                        .order_by(asc(func.lower(DrinkName.name))).all() 
    other_drinks = filter_drink_list(can_make_dict, other_drinks)
    process_ingredients(other_drinks)
    
    shots = []
    for s in db.session.query(Booze).filter(Booze.shotworthy != 0 ).order_by(asc(func.lower(Booze.name))).all():
        print "s=%s" % str(s)
        dispenser = app.mixer.find_dispenser( s.id )
        if dispenser != None:
            shots.append( { 'name': s.name, 'desc': s.desc, 'dispenser':dispenser.id, 'dispenser_actual': dispenser.actual  })
    print "shots=%s" % str(shots)
    
            
    return render_template("index", 
                           top_drinks=top_drinks, 
                           other_drinks=other_drinks,
                           shots=shots,
                           includeOfflineCheckbox=True,
                           title="Bartendro")

def shotbot():
    disp = db.session.query(Dispenser).all()
    disp = disp[:app.driver.count()]
    return render_template("shotbot", 
                           options=app.options, 
                           dispensers=disp, 
                           count=app.driver.count(), 
                           title="ShotBot")
