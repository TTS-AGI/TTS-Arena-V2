#!/usr/bin/env python3
"""
Database migration script for TTS Arena analytics columns and new security features.

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


def check_table_exists(cursor, table_name):
    """Check if a table exists in the database."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None


def create_timeout_and_campaign_tables(cursor):
    """Create the new timeout and campaign tables."""
    tables_created = []
    
    # Create coordinated_voting_campaign table
    if not check_table_exists(cursor, "coordinated_voting_campaign"):
        cursor.execute("""
            CREATE TABLE coordinated_voting_campaign (
                id INTEGER PRIMARY KEY,
                model_id VARCHAR(100) NOT NULL,
                model_type VARCHAR(20) NOT NULL,
                detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                time_window_hours INTEGER NOT NULL,
                vote_count INTEGER NOT NULL,
                user_count INTEGER NOT NULL,
                confidence_score REAL NOT NULL,
                status VARCHAR(20) DEFAULT 'active',
                admin_notes TEXT,
                resolved_by INTEGER,
                resolved_at DATETIME,
                FOREIGN KEY (model_id) REFERENCES model (id),
                FOREIGN KEY (resolved_by) REFERENCES user (id)
            )
        """)
        tables_created.append("coordinated_voting_campaign")
        click.echo("✅ Created table 'coordinated_voting_campaign'")
    else:
        click.echo("⏭️  Table 'coordinated_voting_campaign' already exists, skipping")
    
    # Create campaign_participant table
    if not check_table_exists(cursor, "campaign_participant"):
        cursor.execute("""
            CREATE TABLE campaign_participant (
                id INTEGER PRIMARY KEY,
                campaign_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                votes_in_campaign INTEGER NOT NULL,
                first_vote_at DATETIME NOT NULL,
                last_vote_at DATETIME NOT NULL,
                suspicion_level VARCHAR(20) NOT NULL,
                FOREIGN KEY (campaign_id) REFERENCES coordinated_voting_campaign (id),
                FOREIGN KEY (user_id) REFERENCES user (id)
            )
        """)
        tables_created.append("campaign_participant")
        click.echo("✅ Created table 'campaign_participant'")
    else:
        click.echo("⏭️  Table 'campaign_participant' already exists, skipping")
    
    # Create user_timeout table
    if not check_table_exists(cursor, "user_timeout"):
        cursor.execute("""
            CREATE TABLE user_timeout (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                reason VARCHAR(500) NOT NULL,
                timeout_type VARCHAR(50) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                created_by INTEGER,
                is_active BOOLEAN DEFAULT 1,
                cancelled_at DATETIME,
                cancelled_by INTEGER,
                cancel_reason VARCHAR(500),
                related_campaign_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES user (id),
                FOREIGN KEY (created_by) REFERENCES user (id),
                FOREIGN KEY (cancelled_by) REFERENCES user (id),
                FOREIGN KEY (related_campaign_id) REFERENCES coordinated_voting_campaign (id)
            )
        """)
        tables_created.append("user_timeout")
        click.echo("✅ Created table 'user_timeout'")
    else:
        click.echo("⏭️  Table 'user_timeout' already exists, skipping")
    
    # Create consumed_sentence table
    if not check_table_exists(cursor, "consumed_sentence"):
        cursor.execute("""
            CREATE TABLE consumed_sentence (
                id INTEGER PRIMARY KEY,
                sentence_hash VARCHAR(64) UNIQUE NOT NULL,
                sentence_text TEXT NOT NULL,
                consumed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                session_id VARCHAR(100),
                usage_type VARCHAR(20) NOT NULL
            )
        """)
        # Create index on sentence_hash for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_consumed_sentence_sentence_hash ON consumed_sentence (sentence_hash)")
        tables_created.append("consumed_sentence")
        click.echo("✅ Created table 'consumed_sentence' with index")
    else:
        click.echo("⏭️  Table 'consumed_sentence' already exists, skipping")
    
    return tables_created


def add_analytics_columns_and_tables(db_path):
    """Add analytics columns and create new security tables."""
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
            ("cache_hit", "BOOLEAN"),
            ("sentence_hash", "VARCHAR(64)"),
            ("sentence_origin", "VARCHAR(20)"),
            ("counts_for_public_leaderboard", "BOOLEAN DEFAULT 1")
        ]
        
        # Define the columns to add to user table
        user_columns_to_add = [
            ("hf_account_created", "DATETIME"),
            ("show_in_leaderboard", "BOOLEAN DEFAULT 1")
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
        
        # Create new security tables
        click.echo("🔒 Creating security and timeout management tables...")
        tables_created = create_timeout_and_campaign_tables(cursor)
        
        # Create indexes for new columns
        click.echo("📊 Creating indexes for performance...")
        try:
            # Index on vote.sentence_hash for origin tracking queries
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_vote_sentence_hash ON vote (sentence_hash)")
            click.echo("✅ Created index on vote.sentence_hash")
        except sqlite3.Error as e:
            click.echo(f"⚠️  Note: Could not create vote.sentence_hash index: {e}")
        
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
        
        if tables_created:
            click.echo(f"\n🔒 Successfully created {len(tables_created)} security tables:")
            for table in tables_created:
                click.echo(f"   • {table}")
        
        if not added_columns and not skipped_columns and not tables_created:
            click.echo("❌ No columns or tables were processed")
            return False
        
        click.echo(f"\n✨ Migration completed successfully!")
        
        if tables_created:
            click.echo("\n🚨 New Security Features Enabled:")
            click.echo("   • Automatic coordinated voting campaign detection")
            click.echo("   • User timeout management")
            click.echo("   • Sentence consumption tracking (no reuse)")
            click.echo("   • Vote origin tracking (dataset vs custom)")
            click.echo("   • Public leaderboard integrity protection")
            click.echo("   • Admin panels for security monitoring")
            click.echo("\nNew admin panel sections:")
            click.echo("   • /admin/timeouts - Manage user timeouts")
            click.echo("   • /admin/campaigns - View coordinated voting campaigns")
            click.echo("\nLeaderboard Changes:")
            click.echo("   • Public leaderboard: Only unconsumed dataset sentences count")
            click.echo("   • Personal leaderboard: All votes (dataset + custom) included")
            click.echo("   • Each sentence can only be used once for public rankings")
        
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
    Add analytics columns and security tables to the TTS Arena database.
    
    This migration adds:
    - Vote analytics (session duration, IP, user agent, etc.)
    - Sentence origin tracking (dataset vs custom)
    - Sentence consumption tracking (prevent reuse)
    - Security features (coordinated voting detection, user timeouts)
    - Leaderboard integrity protection
    
    DATABASE_PATH: Path to the SQLite database file (e.g., instance/tts_arena.db)
    """
    click.echo("🚀 TTS Arena Migration Tool")
    click.echo("Analytics + Security + Vote Origin Tracking")
    click.echo("=" * 50)
    
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
        click.echo("\nThe following columns would be added to the 'vote' table:")
        click.echo("   • session_duration_seconds (REAL)")
        click.echo("   • ip_address_partial (VARCHAR(20))")
        click.echo("   • user_agent (VARCHAR(500))")
        click.echo("   • generation_date (DATETIME)")
        click.echo("   • cache_hit (BOOLEAN)")
        click.echo("   • sentence_hash (VARCHAR(64))")
        click.echo("   • sentence_origin (VARCHAR(20))")
        click.echo("   • counts_for_public_leaderboard (BOOLEAN DEFAULT 1)")
        click.echo("\nThe following columns would be added to the 'user' table:")
        click.echo("   • hf_account_created (DATETIME)")
        click.echo("   • show_in_leaderboard (BOOLEAN DEFAULT 1)")
        click.echo("\nThe following security tables would be created:")
        click.echo("   • coordinated_voting_campaign - Track detected voting campaigns")
        click.echo("   • campaign_participant - Track users involved in campaigns")
        click.echo("   • user_timeout - Manage user timeouts/bans")
        click.echo("   • consumed_sentence - Track sentence usage for security")
        click.echo("\nIndexes would be created:")
        click.echo("   • ix_vote_sentence_hash - For vote origin tracking")
        click.echo("   • ix_consumed_sentence_sentence_hash - For sentence consumption queries")
        click.echo("\nRun without --dry-run to apply changes.")
        return
    
    # Confirm before proceeding
    if not click.confirm(f"\n⚠️  This will modify the database at {db_path}. Continue?"):
        click.echo("❌ Migration cancelled")
        sys.exit(0)
    
    # Perform the migration
    click.echo("\n🔧 Starting migration...")
    success = add_analytics_columns_and_tables(str(db_path))
    
    if success:
        click.echo("\n🎊 Migration completed successfully!")
        click.echo("You can now restart your TTS Arena application to use all new features.")
    else:
        click.echo("\n💥 Migration failed!")
        sys.exit(1)


if __name__ == "__main__":
    migrate() 