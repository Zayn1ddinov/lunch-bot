import sqlite3
from datetime import datetime


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                name     TEXT NOT NULL,
                username TEXT
            );
            -- заявки: одна строка на человека в день; portions = 0 — отменил
            CREATE TABLE IF NOT EXISTS orders (
                day        TEXT NOT NULL,
                user_id    INTEGER NOT NULL,
                portions   INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (day, user_id)
            );
            -- опрос дня: сообщение в группе и статус (closed = 1 — отчёт отправлен)
            CREATE TABLE IF NOT EXISTS days (
                day        TEXT PRIMARY KEY,
                chat_id    INTEGER,
                message_id INTEGER,
                closed     INTEGER NOT NULL DEFAULT 0
            );
            -- настройки из панели админа (перекрывают значения из .env)
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def get_setting(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def save_user(self, user_id: int, name: str, username: str | None):
        self.conn.execute(
            """INSERT INTO users (user_id, name, username) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, username = excluded.username""",
            (user_id, name, username),
        )
        self.conn.commit()

    # --- дни ---
    def get_day(self, day: str):
        return self.conn.execute("SELECT * FROM days WHERE day = ?", (day,)).fetchone()

    def open_day(self, day: str, chat_id: int, message_id: int):
        self.conn.execute(
            """INSERT INTO days (day, chat_id, message_id, closed) VALUES (?, ?, ?, 0)
               ON CONFLICT(day) DO UPDATE SET chat_id = excluded.chat_id, message_id = excluded.message_id""",
            (day, chat_id, message_id),
        )
        self.conn.commit()

    def is_open(self, day: str) -> bool:
        row = self.get_day(day)
        return row is not None and row["closed"] == 0

    def close_day(self, day: str):
        self.conn.execute("UPDATE days SET closed = 1 WHERE day = ?", (day,))
        self.conn.commit()

    def cancel_day(self, day: str):
        """Отмена обеда: день закрыт, заявки удалены (не попадают в статистику)."""
        self.conn.execute("UPDATE days SET closed = 1 WHERE day = ?", (day,))
        self.conn.execute("DELETE FROM orders WHERE day = ?", (day,))
        self.conn.commit()

    # --- заявки ---
    def set_order(self, day: str, user_id: int, portions: int):
        self.conn.execute(
            """INSERT INTO orders (day, user_id, portions, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(day, user_id) DO UPDATE SET portions = excluded.portions,
                                                       updated_at = excluded.updated_at""",
            (day, user_id, portions, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def day_orders(self, day: str):
        """Список (имя, порций) в порядке записи, только с порциями > 0."""
        return self.conn.execute(
            """SELECT u.name, o.portions FROM orders o JOIN users u ON u.user_id = o.user_id
               WHERE o.day = ? AND o.portions > 0 ORDER BY o.updated_at""",
            (day,),
        ).fetchall()

    def stats(self, date_from: str, date_to: str):
        """Сколько дней каждый обедал и сколько порций взял за период (включительно)."""
        return self.conn.execute(
            """SELECT u.name, COUNT(*) AS days, SUM(o.portions) AS portions
               FROM orders o JOIN users u ON u.user_id = o.user_id
               WHERE o.day BETWEEN ? AND ? AND o.portions > 0
               GROUP BY o.user_id ORDER BY days DESC, u.name COLLATE NOCASE""",
            (date_from, date_to),
        ).fetchall()

    def period_orders(self, date_from: str, date_to: str):
        """Все заявки за период: (day, name, portions), по дням."""
        return self.conn.execute(
            """SELECT o.day, u.name, o.portions FROM orders o JOIN users u ON u.user_id = o.user_id
               WHERE o.day BETWEEN ? AND ? AND o.portions > 0 ORDER BY o.day, o.updated_at""",
            (date_from, date_to),
        ).fetchall()
