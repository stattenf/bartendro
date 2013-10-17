# -*- coding: utf-8 -*-
from bartendro import db
from sqlalchemy.orm import mapper, relationship
from sqlalchemy import Table, Column, Integer, String, MetaData, UnicodeText, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from bartendro.model.drink_name import DrinkName
from operator import attrgetter

DEFAULT_SUGGESTED_DRINK_SIZE = 118 #ml (4 oz)

class Drink(db.Model):
    """
    Defintion of a drink. Pretty bare since name is in drink_name and drink details are in drink_liquid
    """

    __tablename__ = 'drink'
    id = Column(Integer, primary_key=True)
    desc = Column(UnicodeText, nullable=False)
    name_id = Column(Integer, ForeignKey('drink_name.id'), nullable=False)
    sugg_size = Column(Integer)
    popular = Column(Boolean)
    available = Column(Boolean)
    requiresOfflineIngredients = True
    
    query = db.session.query_property()

    def __init__(self, desc = u'', data = None, size = DEFAULT_SUGGESTED_DRINK_SIZE, popular = False, available = True):
        self.name = DrinkName()
        if data: 
            self.update(data)
            self.requiresOfflineIngredients = self.hasOfflineIngredients()
            return
        self.desc = desc
        self.size = size
        self.popular = popular
        self.available = available
        self.sugg_size = 0
        
        db.session.add(self)
    
    def hasOfflineIngredients(self):
        result = False
        
        boozes = db.session.query("booze_id").from_statement("SELECT booze_id FROM drink_booze db WHERE db.drink_id = :d" ).params( d = self.id ).all()
        offline_boozes = db.session.query("id").from_statement("""SELECT bg.id FROM booze bg WHERE bg.offline > 0""").all()

        result = len(filter( lambda x:x in boozes, offline_boozes )) > 0

        return result
    
    def process_ingredients(self):
        ing = []

        offline_boozes = set()
        for i in db.session.query("id").from_statement("""SELECT bg.id FROM booze bg WHERE bg.offline > 0""").all():
            offline_boozes.add( i[0] )
        print "offline_boozes=%s" % str(offline_boozes)

        self.requiresOfflineIngredients = False

        self.drink_boozes = sorted(self.drink_boozes, key=attrgetter('booze.abv', 'booze.name'), reverse=True)
        for drinkBooze in self.drink_boozes:
            print "drinkBooze=%s" % str(drinkBooze)
            offline = drinkBooze.booze.id in offline_boozes
            self.requiresOfflineIngredients = self.requiresOfflineIngredients or offline
            ing.append({ 'name' : drinkBooze.booze.name, 
                         'id' : drinkBooze.booze.id, 
                         'parts' : drinkBooze.value, 
                         'type' : drinkBooze.booze.type,
                         'offline': offline
                       })
        self.ingredients = ing

    def __repr__(self):
        return "<Drink>(id=%d,%s,%s,%s)>" % (self.id or -1, self.name.name, self.desc, " ".join(["<DrinkBooze>(%d)" % (db.id or -1) for db in self.drink_boozes]))

