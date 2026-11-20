"""Backup a caldo del database SQLite (API di backup di sqlite3, sicura anche a sito acceso)."""
import os
import sqlite3
from datetime import datetime

from flask import current_app


def db_path():
    return current_app.config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///")


def make_backup():
    folder = current_app.config["BACKUP_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"endurance-{datetime.now():%Y%m%d-%H%M%S}.db")
    src = sqlite3.connect(db_path())
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    # rotazione: tiene solo gli ultimi BACKUP_KEEP
    files = sorted(f for f in os.listdir(folder) if f.startswith("endurance-") and f.endswith(".db"))
    for old in files[:-current_app.config["BACKUP_KEEP"]]:
        os.remove(os.path.join(folder, old))
    return dest
