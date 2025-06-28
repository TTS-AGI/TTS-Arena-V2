#!/usr/bin/env python3
"""
Migration script to add ConsumedSentence table for tracking used sentences.
Run this script once to update existing databases.
"""

import os
import sys
from flask import Flask
from models import db, ConsumedSentence

def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URI", "sqlite:///tts_arena.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    db.init_app(app)
    return app

def migrate():
    app = create_app()
    
    with app.app_context():
        try:
            # Create the ConsumedSentence table
            db.create_all()
            print("✅ Successfully created ConsumedSentence table")
            
            # Check if table was created
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'consumed_sentence' in tables:
                print("✅ ConsumedSentence table confirmed in database")
            else:
                print("❌ ConsumedSentence table not found after creation")
                
        except Exception as e:
            print(f"❌ Error during migration: {e}")
            return False
            
    return True

if __name__ == "__main__":
    print("Running ConsumedSentence table migration...")
    if migrate():
        print("Migration completed successfully!")
    else:
        print("Migration failed!")
        sys.exit(1) 