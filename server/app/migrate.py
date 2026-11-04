"""Run database schema migrations before starting API/Worker services."""
from app.core.db import migrate_db


if __name__ == "__main__":
    migrate_db()
