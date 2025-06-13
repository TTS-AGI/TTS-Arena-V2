#!/usr/bin/env python3
"""
Database migration script for TTS Arena analytics columns.

Usage:
    python migrate.py database.db
    python migrate.py instance/tts_arena.db
"""

import click
import sqlite3
import sys
import os
from pathlib import Path


def check_column_exists(cursor, table_name, column_name):
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def add_analytics_columns(db_path):
    """Add analytics columns to the vote table."""
    if not os.path.exists(db_path):
        click.echo(f"❌ Database file not found: {db_path}", err=True)
        return False
    
    try:
        # Connect to the database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if vote table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vote'")
        if not cursor.fetchone():
            click.echo("❌ Vote table not found in database", err=True)
            return False
        
        # Define the columns to add to vote table
        vote_columns_to_add = [
            ("session_duration_seconds", "REAL"),
            ("ip_address_partial", "VARCHAR(20)"),
            ("user_agent", "VARCHAR(500)"),
            ("generation_date", "DATETIME"),
            ("cache_hit", "BOOLEAN")
        ]
        
        # Define the columns to add to user table
        user_columns_to_add = [
            ("hf_account_created", "DATETIME")
        ]
        
        added_columns = []
        skipped_columns = []
        
        # Add vote table columns
        click.echo("📊 Processing vote table columns...")
        for column_name, column_type in vote_columns_to_add:
            if check_column_exists(cursor, "vote", column_name):
                skipped_columns.append(f"vote.{column_name}")
                click.echo(f"⏭️  Column 'vote.{column_name}' already exists, skipping")
            else:
                try:
                    cursor.execute(f"ALTER TABLE vote ADD COLUMN {column_name} {column_type}")
                    added_columns.append(f"vote.{column_name}")
                    click.echo(f"✅ Added column 'vote.{column_name}' ({column_type})")
                except sqlite3.Error as e:
                    click.echo(f"❌ Failed to add column 'vote.{column_name}': {e}", err=True)
                    conn.rollback()
                    return False
        
        # Add user table columns
        click.echo("👤 Processing user table columns...")
        for column_name, column_type in user_columns_to_add:
            if check_column_exists(cursor, "user", column_name):
                skipped_columns.append(f"user.{column_name}")
                click.echo(f"⏭️  Column 'user.{column_name}' already exists, skipping")
            else:
                try:
                    cursor.execute(f"ALTER TABLE user ADD COLUMN {column_name} {column_type}")
                    added_columns.append(f"user.{column_name}")
                    click.echo(f"✅ Added column 'user.{column_name}' ({column_type})")
                except sqlite3.Error as e:
                    click.echo(f"❌ Failed to add column 'user.{column_name}': {e}", err=True)
                    conn.rollback()
                    return False
        
        # Commit the changes
        conn.commit()
        conn.close()
        
        # Summary
        if added_columns:
            click.echo(f"\n🎉 Successfully added {len(added_columns)} analytics columns:")
            for col in added_columns:
                click.echo(f"   • {col}")
        
        if skipped_columns:
            click.echo(f"\n⏭️  Skipped {len(skipped_columns)} existing columns:")
            for col in skipped_columns:
                click.echo(f"   • {col}")
        
        if not added_columns and not skipped_columns:
            click.echo("❌ No columns were processed")
            return False
        
        click.echo(f"\n✨ Migration completed successfully!")
        return True
        
    except sqlite3.Error as e:
        click.echo(f"❌ Database error: {e}", err=True)
        return False
    except Exception as e:
        click.echo(f"❌ Unexpected error: {e}", err=True)
        return False


@click.command()
@click.argument('database_path', type=click.Path())
@click.option('--dry-run', is_flag=True, help='Show what would be done without making changes')
@click.option('--backup', is_flag=True, help='Create a backup before migration')
def migrate(database_path, dry_run, backup):
    """
    Add analytics columns to the TTS Arena database.
    
    DATABASE_PATH: Path to the SQLite database file (e.g., instance/tts_arena.db)
    """
    click.echo("🚀 TTS Arena Analytics Migration Tool")
    click.echo("=" * 40)
    
    # Resolve the database path
    db_path = Path(database_path).resolve()
    click.echo(f"📁 Database: {db_path}")
    
    if not db_path.exists():
        click.echo(f"❌ Database file not found: {db_path}", err=True)
        sys.exit(1)
    
    # Create backup if requested
    if backup:
        backup_path = db_path.with_suffix(f"{db_path.suffix}.backup")
        try:
            import shutil
            shutil.copy2(db_path, backup_path)
            click.echo(f"💾 Backup created: {backup_path}")
        except Exception as e:
            click.echo(f"❌ Failed to create backup: {e}", err=True)
            sys.exit(1)
    
    if dry_run:
        click.echo("\n🔍 DRY RUN MODE - No changes will be made")
        click.echo("The following columns would be added to the 'vote' table:")
        click.echo("   • session_duration_seconds (REAL)")
        click.echo("   • ip_address_partial (VARCHAR(20))")
        click.echo("   • user_agent (VARCHAR(500))")
        click.echo("   • generation_date (DATETIME)")
        click.echo("   • cache_hit (BOOLEAN)")
        click.echo("\nThe following columns would be added to the 'user' table:")
        click.echo("   • hf_account_created (DATETIME)")
        click.echo("\nRun without --dry-run to apply changes.")
        return
    
    # Confirm before proceeding
    if not click.confirm(f"\n⚠️  This will modify the database at {db_path}. Continue?"):
        click.echo("❌ Migration cancelled")
        sys.exit(0)
    
    # Perform the migration
    click.echo("\n🔧 Starting migration...")
    success = add_analytics_columns(str(db_path))
    
    if success:
        click.echo("\n🎊 Migration completed successfully!")
        click.echo("You can now restart your TTS Arena application to use analytics features.")
    else:
        click.echo("\n💥 Migration failed!")
        sys.exit(1)


if __name__ == "__main__":
    migrate() 