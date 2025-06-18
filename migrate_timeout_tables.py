#!/usr/bin/env python3
"""
Migration script to add coordinated voting campaign detection and user timeout tables.
Run this script to add the new tables to your existing database.
"""

import os
import sys
from datetime import datetime

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from models import db, CoordinatedVotingCampaign, CampaignParticipant, UserTimeout

def migrate_database():
    """Add the new tables to the database"""
    with app.app_context():
        try:
            print("Creating new tables for coordinated voting detection and user timeouts...")
            
            # Create the new tables
            db.create_all()
            
            print("✅ Successfully created new tables:")
            print("  - coordinated_voting_campaign")
            print("  - campaign_participant") 
            print("  - user_timeout")
            
            print("\nMigration completed successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Error during migration: {str(e)}")
            return False

if __name__ == "__main__":
    print("TTS Arena - Database Migration for Timeout and Campaign Management")
    print("=" * 70)
    
    # Confirm with user
    response = input("This will add new tables to your database. Continue? (y/N): ")
    if response.lower() != 'y':
        print("Migration cancelled.")
        sys.exit(0)
    
    success = migrate_database()
    
    if success:
        print("\n" + "=" * 70)
        print("Migration completed! You can now:")
        print("1. Access the new admin panels for timeout and campaign management")
        print("2. Automatic coordinated voting detection is now active")
        print("3. Users involved in coordinated campaigns will be automatically timed out")
        print("\nNew admin panel sections:")
        print("- /admin/timeouts - Manage user timeouts")
        print("- /admin/campaigns - View and manage coordinated voting campaigns")
    else:
        print("\n❌ Migration failed. Please check the error messages above.")
        sys.exit(1) 