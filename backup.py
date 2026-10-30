"""Резервная копия базы: backups/lunch_ГГГГ-ММ-ДД.db, хранятся последние 30 копий.

Запуск: python backup.py [папка]  — можно при работающем боте.
"""
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

KEEP = 30

os.chdir(Path(__file__).resolve().parent)
load_dotenv()
folder = Path(sys.argv[1] if len(sys.argv) > 1 else "backups")
folder.mkdir(parents=True, exist_ok=True)
target = folder / f"lunch_{date.today()}.db"

src = sqlite3.connect(os.getenv("DB_PATH", "lunch.db"))
dst = sqlite3.connect(target)
src.backup(dst)  # корректная копия даже во время записи
dst.close()
src.close()

for old in sorted(folder.glob("lunch_*.db"))[:-KEEP]:
    old.unlink()
print(f"Копия сохранена: {target}")
