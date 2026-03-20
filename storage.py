from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


class PluginLocalStorage:
    """Persist plugin state in a local SQLite database under AstrBot's plugin_data."""

    def __init__(self, base_dir: Path, legacy_base_dirs: Optional[list[Path]] = None):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "ts3_tracker.db"
        self.legacy_base_dirs = self._normalize_legacy_dirs(legacy_base_dirs or [])
        self._migrate_legacy_db_if_needed()
        self._init_db()
        self._migrate_legacy_json_files()
        self._disable_legacy_notify_targets_once()

    def save_last_status(self, payload: dict[str, Any]) -> None:
        self.set_meta("last_status_json", payload)

    def load_notify_targets(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT target_id
                FROM notify_targets
                WHERE enabled = 1
                ORDER BY created_at ASC, target_id ASC
                """
            ).fetchall()
        return [str(row["target_id"]) for row in rows]

    def add_notify_target(self, target: str) -> bool:
        target = str(target).strip()
        if not target:
            return False

        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM notify_targets WHERE target_id = ?",
                (target,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO notify_targets (
                        target_id, enabled, created_at, last_success_at, last_error_at, last_error
                    ) VALUES (?, 1, ?, NULL, NULL, NULL)
                    """,
                    (target, now),
                )
                return True

            if int(row["enabled"]) == 0:
                conn.execute(
                    """
                    UPDATE notify_targets
                    SET enabled = 1, last_error = NULL
                    WHERE target_id = ?
                    """,
                    (target,),
                )
                return True

        return False

    def disable_notify_target(self, target: str) -> bool:
        target = str(target).strip()
        if not target:
            return False

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE notify_targets
                SET enabled = 0
                WHERE target_id = ? AND enabled = 1
                """,
                (target,),
            )
            return int(cursor.rowcount or 0) > 0

    def is_notify_target_enabled(self, target: str) -> bool:
        target = str(target).strip()
        if not target:
            return False

        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM notify_targets WHERE target_id = ?",
                (target,),
            ).fetchone()
        return bool(row and int(row["enabled"]) == 1)

    def mark_notify_target_success(self, target: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notify_targets
                SET last_success_at = ?, last_error_at = NULL, last_error = NULL
                WHERE target_id = ?
                """,
                (int(time.time()), target),
            )

    def mark_notify_target_error(self, target: str, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notify_targets
                SET last_error_at = ?, last_error = ?
                WHERE target_id = ?
                """,
                (int(time.time()), str(error_message), target),
            )

    def is_baseline_initialized(self, server_key: str) -> bool:
        return bool(self.get_meta(self._baseline_meta_key(server_key), False))

    def set_baseline_initialized(self, server_key: str, initialized: bool) -> None:
        self.set_meta(self._baseline_meta_key(server_key), bool(initialized))

    def reset_runtime_state(self, server_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM active_sessions WHERE server_key = ?",
                (server_key,),
            )
        self.set_baseline_initialized(server_key, False)

    def clear_database(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM active_sessions")
            conn.execute("DELETE FROM session_history")
            conn.execute("DELETE FROM notify_targets")
            conn.execute("DELETE FROM meta")

        # Keep the legacy migration marker so old local files are not re-imported
        # after an intentional manual reset.
        self.set_meta("legacy_json_migrated_v1", True)

    def load_active_sessions(self, server_key: str) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_key, unique_id, nickname, channel_name, client_ip, online_at, last_seen_at
                FROM active_sessions
                WHERE server_key = ?
                """,
                (server_key,),
            ).fetchall()

        return {
            str(row["session_key"]): {
                "key": str(row["session_key"]),
                "unique_id": str(row["unique_id"]),
                "nickname": str(row["nickname"]),
                "channel_name": str(row["channel_name"]),
                "client_ip": str(row["client_ip"]),
                "start_ts": int(row["online_at"]),
                "last_seen_ts": int(row["last_seen_at"]),
            }
            for row in rows
        }

    def replace_active_sessions(self, server_key: str, sessions: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM active_sessions WHERE server_key = ?",
                (server_key,),
            )
            if not sessions:
                return
            conn.executemany(
                """
                INSERT INTO active_sessions (
                    server_key, session_key, unique_id, nickname, channel_name, client_ip, online_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        server_key,
                        str(session["key"]),
                        str(session.get("unique_id", "")),
                        str(session["nickname"]),
                        str(session["channel_name"]),
                        str(session.get("client_ip", "")),
                        int(session["start_ts"]),
                        int(session["last_seen_ts"]),
                    )
                    for session in sessions
                ],
            )

    def record_session_history(
        self,
        server_key: str,
        session: dict[str, Any],
        offline_detected_at: int,
    ) -> None:
        online_at = int(session["start_ts"])
        last_seen_at = int(session.get("last_seen_ts", online_at))
        offline_detected_at = int(offline_detected_at)
        duration_seconds = max(0, offline_detected_at - online_at)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_history (
                    server_key,
                    session_key,
                    unique_id,
                    nickname,
                    channel_name,
                    client_ip,
                    online_at,
                    last_seen_at,
                    offline_detected_at,
                    duration_seconds,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_key,
                    str(session["key"]),
                    str(session.get("unique_id", "")),
                    str(session["nickname"]),
                    str(session["channel_name"]),
                    str(session.get("client_ip", "")),
                    online_at,
                    last_seen_at,
                    offline_detected_at,
                    duration_seconds,
                    int(time.time()),
                ),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(str(row["value"]))
        except Exception:
            return default

    def set_meta(self, key: str, value: Any) -> None:
        serialized = json.dumps(value, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, serialized),
            )

    def _baseline_meta_key(self, server_key: str) -> str:
        return f"baseline_initialized:{server_key}"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notify_targets (
                    target_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    last_success_at INTEGER,
                    last_error_at INTEGER,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS active_sessions (
                    server_key TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    unique_id TEXT NOT NULL DEFAULT '',
                    nickname TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    client_ip TEXT NOT NULL DEFAULT '',
                    online_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    PRIMARY KEY (server_key, session_key)
                );

                CREATE TABLE IF NOT EXISTS session_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_key TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    unique_id TEXT NOT NULL DEFAULT '',
                    nickname TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    client_ip TEXT NOT NULL DEFAULT '',
                    online_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    offline_detected_at INTEGER NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_session_history_server_time
                ON session_history (server_key, offline_detected_at DESC);

                CREATE INDEX IF NOT EXISTS idx_session_history_unique
                ON session_history (server_key, unique_id, offline_detected_at DESC);
                """
            )
            self._ensure_column(conn, "active_sessions", "client_ip", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "session_history", "client_ip", "TEXT NOT NULL DEFAULT ''")

    def _migrate_legacy_json_files(self) -> None:
        if self.get_meta("legacy_json_migrated_v1", False):
            return

        for legacy_dir in self.legacy_base_dirs:
            legacy_last_status_file = legacy_dir / "last_status.json"
            if legacy_last_status_file.exists():
                payload = self._read_json_file(legacy_last_status_file)
                if payload:
                    self.save_last_status(payload)
                    break

        for legacy_dir in self.legacy_base_dirs:
            legacy_notify_targets_file = legacy_dir / "notify_targets.json"
            if not legacy_notify_targets_file.exists():
                continue

            payload = self._read_json_file(legacy_notify_targets_file)
            targets = payload.get("targets", []) if isinstance(payload, dict) else []
            for target in targets:
                self.add_notify_target(str(target))
            if targets:
                break

        self.set_meta("legacy_json_migrated_v1", True)

    def _read_json_file(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _normalize_legacy_dirs(self, legacy_base_dirs: list[Path]) -> list[Path]:
        normalized: list[Path] = []
        current_dir = self.base_dir.resolve()
        for legacy_dir in legacy_base_dirs:
            try:
                resolved = legacy_dir.resolve()
            except Exception:
                continue
            if resolved == current_dir or resolved in normalized:
                continue
            normalized.append(resolved)
        return normalized

    def _migrate_legacy_db_if_needed(self) -> None:
        if self.db_path.exists():
            return

        for legacy_dir in self.legacy_base_dirs:
            legacy_db_path = legacy_dir / "ts3_tracker.db"
            if not legacy_db_path.exists():
                continue

            with sqlite3.connect(str(legacy_db_path)) as source_conn:
                with sqlite3.connect(str(self.db_path)) as target_conn:
                    source_conn.backup(target_conn)
            return

    def _disable_legacy_notify_targets_once(self) -> None:
        if self.get_meta("admin_notify_gate_v1", False):
            return

        with self._connect() as conn:
            conn.execute("UPDATE notify_targets SET enabled = 0 WHERE enabled = 1")

        self.set_meta("admin_notify_gate_v1", True)

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_ddl: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name in existing_columns:
            return
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}"
        )
