# -*- coding: utf-8 -*-
from bartendro import app, db
from flask import Flask, request, jsonify
from bartendro.model.drink import Drink
from bartendro.model.booze import Booze
from bartendro.form.booze import BoozeForm

@app.route('/ws/booze/match/<str>')
def ws_booze(request, str):
    str = str + "%%"
    boozes = db.session.query("id", "name").from_statement("SELECT id, name FROM booze WHERE name LIKE :s").params(s=str).all()
    return jsonify(boozes)

@app.route('/ws/booze/<int:booze_id>')
def ws_booze_id(request, booze_id):
    print "/ws/booze/booze_id=%d, args=%s" % ( booze_id, str(request.args) )
    
    recipe = {}
    for arg in request.args:
        recipe[arg] = int(request.args.get(arg))
    
    print "/ws/booze/ recipie=%s" % str(request.args)
    
    return ""