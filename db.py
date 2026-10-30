import sqlite3
from datetime import datetime

# имя для учёта: официальное, если указано, иначе из профиля Telegram
DISPLAY = "COALESCE(u.official_name, u.name)"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                name          TEXT NOT NULL,          -- имя из профиля Telegram
                username      TEXT,
                official_name TEXT,                   -- имя и фамилия для учёта
                name_asked    INTEGER NOT NULL DEFAULT 0,
                can_dm        INTEGER NOT NULL DEFAULT 0  -- нажал Start, можно писать в личку
            );
            -- заявки: одна строка на человека в день; portions = 0 — отменил
            CREATE TABLE IF NOT EXISTS orders (
                day        TEXT NOT NULL,
                user_id    INTEGER NOT NULL,
                portions   INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (day, user_id)
            );
            -- опрос дня: сообщение в группе, цена обеда в этот день, статус (closed = 1 — отчёт отправлен)
            CREATE TABLE IF NOT EXISTS days (
                day        TEXT PRIMARY KEY,
                chat_id    INTEGER,
                message_id INTEGER,
                closed     INTEGER NOT NULL DEFAULT 0,
                price      INTEGER,
                poll_id    TEXT
            );
            -- настройки из панели админа (перекрывают значения из .env)
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS menu (
                day  TEXT PRIMARY KEY,
                text TEXT NOT NULL
            );
            -- оплаты: status = pending (ждёт проверки) / confirmed / rejected
            CREATE TABLE IF NOT EXISTS payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                amount     INTEGER NOT NULL,
                status     TEXT NOT NULL,
                file_id    TEXT,
                file_type  TEXT,
                created_at TEXT NOT NULL,
                decided_by INTEGER,
                decided_at TEXT,
                note       TEXT
            );
            """
        )
        self._migrate()

    def _migrate(self):
        """Добавляет колонки, которых не было в первых версиях базы."""
        new_columns = {
            "users": {"official_name": "TEXT", "name_asked": "INTEGER NOT NULL DEFAULT 0",
                      "can_dm": "INTEGER NOT NULL DEFAULT 0"},
            "days": {"price": "INTEGER", "poll_id": "TEXT"},
        }
        for table, columns in new_columns.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in columns.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    # --- настройки ---
    def get_setting(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- пользователи ---
    def save_user(self, user_id: int, name: str, username: str | None):
        self.conn.execute(
            """INSERT INTO users (user_id, name, username) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, username = excluded.username""",
            (user_id, name, username),
        )
        self.conn.commit()

    def get_user(self, user_id: int):
        return self.conn.execute(
            f"SELECT u.*, {DISPLAY} AS display FROM users u WHERE u.user_id = ?", (user_id,)
        ).fetchone()

    def set_official_name(self, user_id: int, name: str | None):
        self.conn.execute("UPDATE users SET official_name = ?, name_asked = 1 WHERE user_id = ?", (name, user_id))
        self.conn.commit()

    def mark_name_asked(self, user_id: int):
        self.conn.execute("UPDATE users SET name_asked = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def set_can_dm(self, user_id: int, value: bool):
        self.conn.execute("UPDATE users SET can_dm = ? WHERE user_id = ?", (int(value), user_id))
        self.conn.commit()

    def all_users(self):
        return self.conn.execute(
            f"SELECT u.*, {DISPLAY} AS display FROM users u ORDER BY display COLLATE NOCASE"
        ).fetchall()

    # --- дни ---
    def get_day(self, day: str):
        return self.conn.execute("SELECT * FROM days WHERE day = ?", (day,)).fetchone()

    def day_by_poll(self, poll_id: str):
        return self.conn.execute("SELECT * FROM days WHERE poll_id = ?", (poll_id,)).fetchone()

    def open_day(self, day: str, chat_id: int, message_id: int, price: int, poll_id: str | None = None):
        self.conn.execute(
            """INSERT INTO days (day, chat_id, message_id, closed, price, poll_id) VALUES (?, ?, ?, 0, ?, ?)
               ON CONFLICT(day) DO UPDATE SET chat_id = excluded.chat_id, message_id = excluded.message_id,
                                              price = excluded.price, poll_id = excluded.poll_id""",
            (day, chat_id, message_id, price, poll_id),
        )
        self.conn.commit()

    def is_open(self, day: str) -> bool:
        row = self.get_day(day)
        return row is not None and row["closed"] == 0

    def close_day(self, day: str, price: int):
        self.conn.execute("UPDATE days SET closed = 1, price = ? WHERE day = ?", (price, day))
        self.conn.commit()

    def cancel_day(self, day: str):
        """Отмена обеда: день закрыт, заявки удалены (не попадают в статистику и долг)."""
        self.conn.execute("UPDATE days SET closed = 1 WHERE day = ?", (day,))
        self.conn.execute("DELETE FROM orders WHERE day = ?", (day,))
        self.conn.commit()

    # --- заявки ---
    def set_order(self, day: str, user_id: int, portions: int):
        self.conn.execute(
            """INSERT INTO orders (day, user_id, portions, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(day, user_id) DO UPDATE SET portions = excluded.portions,
                                                       updated_at = excluded.updated_at""",
            (day, user_id, portions, now()),
        )
        self.conn.commit()

    def day_orders(self, day: str):
        """Список (имя, порций) в порядке записи, только с порциями > 0."""
        return self.conn.execute(
            f"""SELECT {DISPLAY} AS name, o.portions FROM orders o JOIN users u ON u.user_id = o.user_id
                WHERE o.day = ? AND o.portions > 0 ORDER BY o.updated_at""",
            (day,),
        ).fetchall()

    def user_lunches(self, user_id: int, date_from: str, date_to: str):
        return self.conn.execute(
            """SELECT day, portions FROM orders
               WHERE user_id = ? AND day BETWEEN ? AND ? AND portions > 0 ORDER BY day""",
            (user_id, date_from, date_to),
        ).fetchall()

    def stats(self, date_from: str, date_to: str, price: int):
        """Сколько дней каждый обедал, сколько порций и на какую сумму за период (включительно)."""
        return self.conn.execute(
            f"""SELECT {DISPLAY} AS name, COUNT(*) AS days, SUM(o.portions) AS portions,
                       SUM(o.portions * COALESCE(d.price, ?)) AS charged
                FROM orders o JOIN users u ON u.user_id = o.user_id LEFT JOIN days d ON d.day = o.day
                WHERE o.day BETWEEN ? AND ? AND o.portions > 0
                GROUP BY o.user_id ORDER BY days DESC, name COLLATE NOCASE""",
            (price, date_from, date_to),
        ).fetchall()

    def period_orders(self, date_from: str, date_to: str):
        """Все заявки за период: (day, name, portions), по дням."""
        return self.conn.execute(
            f"""SELECT o.day, {DISPLAY} AS name, o.portions FROM orders o JOIN users u ON u.user_id = o.user_id
                WHERE o.day BETWEEN ? AND ? AND o.portions > 0 ORDER BY o.day, o.updated_at""",
            (date_from, date_to),
        ).fetchall()

    # --- деньги ---
    def balance(self, user_id: int, price: int) -> tuple[int, int, int]:
        """(начислено за всё время, подтверждённые оплаты, оплаты на проверке)."""
        charged = self.conn.execute(
            """SELECT COALESCE(SUM(o.portions * COALESCE(d.price, ?)), 0) FROM orders o
               LEFT JOIN days d ON d.day = o.day WHERE o.user_id = ? AND o.portions > 0""",
            (price, user_id),
        ).fetchone()[0]
        paid, pending = self.conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN status = 'confirmed' THEN amount END), 0),
                      COALESCE(SUM(CASE WHEN status = 'pending' THEN amount END), 0)
               FROM payments WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        return charged, paid, pending

    def balances(self, price: int):
        """Начислено / оплачено / долг по каждому, у кого было хоть что-то."""
        return self.conn.execute(
            f"""SELECT u.user_id, u.can_dm, {DISPLAY} AS name,
                       COALESCE(c.charged, 0) AS charged, COALESCE(p.paid, 0) AS paid,
                       COALESCE(c.charged, 0) - COALESCE(p.paid, 0) AS debt
                FROM users u
                LEFT JOIN (SELECT o.user_id, SUM(o.portions * COALESCE(d.price, ?)) AS charged
                           FROM orders o LEFT JOIN days d ON d.day = o.day WHERE o.portions > 0
                           GROUP BY o.user_id) c ON c.user_id = u.user_id
                LEFT JOIN (SELECT user_id, SUM(amount) AS paid FROM payments WHERE status = 'confirmed'
                           GROUP BY user_id) p ON p.user_id = u.user_id
                WHERE c.charged IS NOT NULL OR p.paid IS NOT NULL
                ORDER BY debt DESC, name COLLATE NOCASE""",
            (price,),
        ).fetchall()

    def add_payment(self, user_id: int, amount: int, status: str, file_id: str | None = None,
                    file_type: str | None = None, decided_by: int | None = None, note: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO payments (user_id, amount, status, file_id, file_type, created_at, decided_by, decided_at, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, amount, status, file_id, file_type, now(), decided_by,
             now() if decided_by else None, note),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_payment(self, payment_id: int):
        return self.conn.execute(
            f"""SELECT p.*, {DISPLAY} AS name FROM payments p JOIN users u ON u.user_id = p.user_id
                WHERE p.id = ?""",
            (payment_id,),
        ).fetchone()

    def decide_payment(self, payment_id: int, status: str, admin_id: int) -> bool:
        """Подтвердить/отклонить; False — если уже обработана другим админом."""
        cur = self.conn.execute(
            "UPDATE payments SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, admin_id, now(), payment_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def pending_payments(self):
        return self.conn.execute(
            f"""SELECT p.*, {DISPLAY} AS name FROM payments p JOIN users u ON u.user_id = p.user_id
                WHERE p.status = 'pending' ORDER BY p.created_at"""
        ).fetchall()

    def user_payments(self, user_id: int, limit: int = 10):
        return self.conn.execute(
            "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
        ).fetchall()

    def period_payments(self, date_from: str, date_to: str):
        return self.conn.execute(
            f"""SELECT p.*, {DISPLAY} AS name FROM payments p JOIN users u ON u.user_id = p.user_id
                WHERE p.status = 'confirmed' AND substr(p.created_at, 1, 10) BETWEEN ? AND ?
                ORDER BY p.created_at""",
            (date_from, date_to),
        ).fetchall()

    # --- меню ---
    def get_menu(self, day: str) -> str | None:
        row = self.conn.execute("SELECT text FROM menu WHERE day = ?", (day,)).fetchone()
        return row["text"] if row else None

    def set_menu(self, day: str, text: str | None):
        if text:
            self.conn.execute(
                "INSERT INTO menu (day, text) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET text = excluded.text",
                (day, text),
            )
        else:
            self.conn.execute("DELETE FROM menu WHERE day = ?", (day,))
        self.conn.commit()
