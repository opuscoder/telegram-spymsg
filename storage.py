"""
SQLite-хранилище для сообщений, полученных через Telegram Business
Connection, и фиксации их удаления.
"""
import json
import sqlite3
import time
from contextlib import contextmanager


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    business_connection_id TEXT NOT NULL,
                    chat_id             INTEGER NOT NULL,
                    msg_id              INTEGER NOT NULL,
                    sender_id           INTEGER,
                    sender_name         TEXT,
                    text                TEXT,
                    media_type          TEXT,
                    file_id             TEXT,
                    date_sent           INTEGER,
                    is_deleted          INTEGER NOT NULL DEFAULT 0,
                    date_deleted        INTEGER,
                    date_logged         INTEGER NOT NULL,
                    PRIMARY KEY (business_connection_id, chat_id, msg_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_deleted "
                "ON messages (is_deleted)"
            )

    def save_message(
        self,
        business_connection_id: str,
        chat_id: int,
        msg_id: int,
        sender_id: int | None,
        sender_name: str | None,
        text: str | None,
        media_type: str | None,
        file_id: str | None,
        date_sent: int | None,
    ):
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO messages
                    (business_connection_id, chat_id, msg_id, sender_id,
                     sender_name, text, media_type, file_id, date_sent,
                     is_deleted, date_logged)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(business_connection_id, chat_id, msg_id) DO UPDATE SET
                    text=excluded.text,
                    media_type=excluded.media_type,
                    file_id=COALESCE(excluded.file_id, messages.file_id),
                    sender_name=excluded.sender_name
                """,
                (
                    business_connection_id,
                    chat_id,
                    msg_id,
                    sender_id,
                    sender_name,
                    text,
                    media_type,
                    file_id,
                    date_sent,
                    int(time.time()),
                ),
            )

    def mark_deleted(self, business_connection_id: str, chat_id: int, msg_id: int):
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE messages SET is_deleted = 1, date_deleted = ?
                WHERE business_connection_id = ? AND chat_id = ? AND msg_id = ?
                      AND is_deleted = 0
                """,
                (int(time.time()), business_connection_id, chat_id, msg_id),
            )
            return cur.rowcount

    def get_message(self, business_connection_id: str, chat_id: int, msg_id: int):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM messages
                WHERE business_connection_id = ? AND chat_id = ? AND msg_id = ?
                """,
                (business_connection_id, chat_id, msg_id),
            ).fetchone()
            return dict(row) if row else None


class State:
    """Небольшой JSON-файл для хранения owner_id, если он не задан в .env."""

    def __init__(self, path: str):
        self.path = path

    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get_owner_id(self) -> int | None:
        return self._read().get("owner_id")

    def set_owner_id(self, owner_id: int):
        data = self._read()
        data["owner_id"] = owner_id
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
