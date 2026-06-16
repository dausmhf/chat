import os
import sys
from pathlib import Path
from sqlalchemy import text
from app.config import settings
from app.storage.database import engine

def run_migrations():
    """
    Reads DATABASE_SCHEMA.sql and executes it on the database.
    """
    print("Migrating database schema from canonical SQL file...")
    schema_path = Path(__file__).parent.parent.parent.resolve() / "DATABASE_SCHEMA.sql"
    if not schema_path.exists():
        print(f"Error: DATABASE_SCHEMA.sql not found at {schema_path}")
        sys.exit(1)

    with open(schema_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Split script into segments/statements by semicolon to execute safely
    # Note: postgres can also run it all as one script, but splitting is cleaner for error detection.
    # To execute a script containing DO blocks, functions or complex PL/pgSQL, executing as a single block is best.
    try:
        with engine.connect() as conn:
            # Wrap in transaction
            with conn.begin():
                # We execute the script content directly
                conn.execute(text(sql_content))
        print("Database migrations applied successfully.")
    except Exception as e:
        print(f"Error applying database migrations: {str(e)}")
        # Don't exit here so we don't crash startup validation if the database is not ready/mocked.

if __name__ == "__main__":
    run_migrations()
