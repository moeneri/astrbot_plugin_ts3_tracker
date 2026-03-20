from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .storage import PluginLocalStorage
    from .ts3_query import Ts3OnlineUser, Ts3ServerStatus


@dataclass
class SessionRecord:
    key: str
    unique_id: str
    nickname: str
    channel_name: str
    client_ip: str
    start_ts: int
    last_seen_ts: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionRecord":
        return cls(
            key=str(payload.get("key", "")),
            unique_id=str(payload.get("unique_id", "")),
            nickname=str(payload.get("nickname", "")),
            channel_name=str(payload.get("channel_name", "")),
            client_ip=str(payload.get("client_ip", "")),
            start_ts=int(payload.get("start_ts", 0)),
            last_seen_ts=int(payload.get("last_seen_ts", 0)),
        )

    @classmethod
    def from_user(cls, user: "Ts3OnlineUser", timestamp: int) -> "SessionRecord":
        return cls(
            key=session_key(user),
            unique_id=user.unique_id,
            nickname=user.nickname,
            channel_name=user.channel_name,
            client_ip=user.client_ip,
            start_ts=timestamp,
            last_seen_ts=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PresenceEvent:
    kind: str
    nickname: str
    start_ts: int
    end_ts: int | None
    total_users: int
    online_names: list[str]


class PresenceTracker:
    def __init__(self, storage: "PluginLocalStorage"):
        self.storage = storage

    def reconcile(self, status: "Ts3ServerStatus", timestamp: float) -> list[PresenceEvent]:
        ts = int(timestamp)
        server_key = build_server_key(status.server_host, status.server_port)
        previous_sessions = {
            key: SessionRecord.from_dict(value)
            for key, value in self.storage.load_active_sessions(server_key).items()
        }
        current_users = {session_key(user): user for user in status.users}
        online_names = [user.nickname for user in status.users]

        if not self.storage.is_baseline_initialized(server_key):
            baseline_sessions = [
                SessionRecord.from_user(user, ts).to_dict()
                for user in current_users.values()
            ]
            self.storage.replace_active_sessions(server_key, baseline_sessions)
            self.storage.set_baseline_initialized(server_key, True)
            return []

        events: list[PresenceEvent] = []
        next_sessions: dict[str, SessionRecord] = {}

        for key, user in current_users.items():
            if key in previous_sessions:
                session = previous_sessions[key]
                session.unique_id = user.unique_id
                session.nickname = user.nickname
                session.channel_name = user.channel_name
                session.client_ip = user.client_ip
                session.last_seen_ts = ts
                next_sessions[key] = session
                continue

            session = SessionRecord.from_user(user, ts)
            next_sessions[key] = session
            events.append(
                PresenceEvent(
                    kind="online",
                    nickname=session.nickname,
                    start_ts=session.start_ts,
                    end_ts=None,
                    total_users=len(online_names),
                    online_names=online_names,
                )
            )

        for key, session in previous_sessions.items():
            if key in current_users:
                continue

            self.storage.record_session_history(server_key, session.to_dict(), ts)
            events.append(
                PresenceEvent(
                    kind="offline",
                    nickname=session.nickname,
                    start_ts=session.start_ts,
                    end_ts=ts,
                    total_users=len(online_names),
                    online_names=online_names,
                )
            )

        self.storage.replace_active_sessions(
            server_key,
            [session.to_dict() for session in next_sessions.values()],
        )
        return events


def session_key(user: "Ts3OnlineUser") -> str:
    return user.unique_id or user.database_id or user.client_id or user.nickname


def build_server_key(host: str, server_port: int) -> str:
    return f"{host}:{server_port}"
