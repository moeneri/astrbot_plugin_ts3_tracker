from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass
from typing import Optional
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register


class Ts3QueryError(Exception):
    """Raised when a ServerQuery request fails."""


ESCAPE_MAP = {
    "\\": "\\\\",
    "/": "\\/",
    " ": "\\s",
    "|": "\\p",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
}

UNESCAPE_MAP = {
    "\\\\": "\\",
    "\\/": "/",
    "\\s": " ",
    "\\p": "|",
    "\\a": "\a",
    "\\b": "\b",
    "\\f": "\f",
    "\\n": "\n",
    "\\r": "\r",
    "\\t": "\t",
    "\\v": "\v",
}


@dataclass
class Ts3OnlineUser:
    nickname: str
    channel_name: str
    client_id: str
    database_id: str
    unique_id: str
    client_ip: str
    connected_duration_seconds: int
    away: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ts3ServerStatus:
    server_name: str
    server_host: str
    server_port: int
    online_count: int
    channel_names: list[str]
    users: list[Ts3OnlineUser]

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "online_count": self.online_count,
            "channel_names": self.channel_names,
            "users": [user.to_dict() for user in self.users],
        }


class Ts3QueryClient:
    def __init__(
        self,
        host: str,
        server_port: int,
        username: str,
        password: str,
        query_port: int = 10011,
        timeout: float = 10.0,
        debug: bool = False,
    ):
        self.host = host
        self.server_port = server_port
        self.query_port = query_port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.debug = debug

    async def fetch_status(self) -> Ts3ServerStatus:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.query_port),
                timeout=self.timeout,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            raise Ts3QueryError(
                f"无法连接到 ServerQuery：{self.host}:{self.query_port} ({exc})"
            ) from exc

        try:
            await self._consume_welcome(reader)
            await self._execute(
                reader,
                writer,
                f"login {self._escape(self.username)} {self._escape(self.password)}",
                "login",
            )
            await self._execute(reader, writer, f"use port={self.server_port}", "use")
            serverinfo_records = await self._execute(reader, writer, "serverinfo", "serverinfo")
            channel_records = await self._execute(reader, writer, "channellist", "channellist")
            client_records = await self._execute(
                reader,
                writer,
                "clientlist -uid -away -ip",
                "clientlist",
            )

            client_details: dict[str, dict[str, str]] = {}
            for client in client_records:
                if client.get("client_type") == "1":
                    continue
                client_id = client.get("clid", "")
                if not client_id:
                    continue
                detail_records = await self._execute(
                    reader,
                    writer,
                    f"clientinfo clid={client_id}",
                    "clientinfo",
                )
                client_details[client_id] = detail_records[0] if detail_records else {}

            await self._write_line(writer, "quit")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        serverinfo = serverinfo_records[0] if serverinfo_records else {}
        channels = {
            channel.get("cid", ""): channel.get("channel_name", "")
            for channel in channel_records
        }
        channel_names = [
            channel.get("channel_name", "")
            for channel in channel_records
            if channel.get("channel_name", "")
        ]

        users: list[Ts3OnlineUser] = []
        for client in client_records:
            if client.get("client_type") == "1":
                continue

            client_id = client.get("clid", "")
            detail = client_details.get(client_id, {})
            users.append(
                Ts3OnlineUser(
                    nickname=client.get("client_nickname", ""),
                    channel_name=channels.get(client.get("cid", ""), ""),
                    client_id=client_id,
                    database_id=client.get("client_database_id", ""),
                    unique_id=client.get("client_unique_identifier", ""),
                    client_ip=client.get("connection_client_ip", ""),
                    connected_duration_seconds=max(
                        0,
                        int(detail.get("connection_connected_time", "0") or "0") // 1000,
                    ),
                    away=client.get("client_away", "0") == "1",
                )
            )

        users.sort(key=lambda item: item.nickname.casefold())

        return Ts3ServerStatus(
            server_name=serverinfo.get("virtualserver_name", ""),
            server_host=self.host,
            server_port=int(serverinfo.get("virtualserver_port", self.server_port)),
            online_count=len(users),
            channel_names=channel_names,
            users=users,
        )

    async def _execute(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str,
        action: str,
    ) -> list[dict[str, str]]:
        await self._write_line(writer, command)
        lines = await self._read_response(reader)
        return self._parse_response(lines, action)

    async def _write_line(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write(f"{line}\n".encode("utf-8"))
        await writer.drain()

    async def _consume_welcome(self, reader: asyncio.StreamReader) -> None:
        for _ in range(3):
            try:
                raw_line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            except asyncio.TimeoutError:
                return

            if not raw_line:
                return

            line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue
            if line.startswith("error "):
                return
            if "TeamSpeak 3 ServerQuery interface" in line:
                return

    async def _read_response(self, reader: asyncio.StreamReader) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                raw_line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                raise Ts3QueryError("ServerQuery 响应超时") from exc

            if not raw_line:
                if lines:
                    return lines
                raise Ts3QueryError("ServerQuery 连接已关闭")

            line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue

            lines.append(line)
            if line.startswith("error "):
                return lines

    def _parse_response(self, lines: list[str], action: str) -> list[dict[str, str]]:
        if not lines:
            return []

        error_line = lines[-1]
        error_info = self._parse_record(error_line.removeprefix("error "))
        error_id = int(error_info.get("id", "-1"))
        if error_id != 0:
            error_msg = error_info.get("msg", "unknown")
            raise Ts3QueryError(f"{action} 失败：{error_msg} (id={error_id})")

        data_lines = lines[:-1]
        if not data_lines:
            return []

        data = "\n".join(data_lines).strip()
        if not data:
            return []

        records: list[dict[str, str]] = []
        for raw_record in data.split("|"):
            raw_record = raw_record.strip()
            if raw_record:
                records.append(self._parse_record(raw_record))
        return records

    def _parse_record(self, payload: str) -> dict[str, str]:
        record: dict[str, str] = {}
        for token in payload.split(" "):
            if not token:
                continue
            if "=" not in token:
                record[token] = ""
                continue
            key, value = token.split("=", 1)
            record[key] = self._unescape(value)
        return record

    def _escape(self, value: str) -> str:
        return "".join(ESCAPE_MAP.get(char, char) for char in value)

    def _unescape(self, value: str) -> str:
        chars: list[str] = []
        index = 0
        while index < len(value):
            if value[index] != "\\" or index + 1 >= len(value):
                chars.append(value[index])
                index += 1
                continue

            escaped = value[index : index + 2]
            chars.append(UNESCAPE_MAP.get(escaped, escaped[1]))
            index += 2

        return "".join(chars)


@register(
    "ts3_tracker",
    "Codex",
    "QQ 发送“上号”后返回 TeamSpeak 3 服务器在线信息。",
    "1.0.0",
    "",
)
class Ts3TrackerPlugin(Star):
    PLAIN_TEXT_TRIGGERS = {"上号"}

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or {}

    @filter.command("上号", alias={"ts", "tsinfo"})
    async def query_server_status(self, event: AstrMessageEvent):
        """查询 TS3 服务器在线信息。"""
        message = await self._build_server_message()
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def query_server_status_plain(self, event: AstrMessageEvent):
        """支持直接发送“上号”触发查询。"""
        content = (event.message_str or "").strip()
        if content not in self.PLAIN_TEXT_TRIGGERS:
            return

        event.stop_event()
        message = await self._build_server_message()
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    async def _build_server_message(self) -> str:
        missing_fields = self._get_missing_required_fields()
        if missing_fields:
            return "TS3 配置不完整，请先填写：" + "、".join(missing_fields)

        client = Ts3QueryClient(
            host=str(self.config.get("server_host", "")).strip(),
            server_port=self._get_int_config("server_port", 9987),
            query_port=self._get_int_config("serverquery_port", 10011),
            username=str(self.config.get("serverquery_username", "")).strip(),
            password=str(self.config.get("serverquery_password", "")).strip(),
            timeout=10.0,
            debug=self._get_bool_config("debug", False),
        )

        try:
            status = await client.fetch_status()
        except Ts3QueryError as exc:
            logger.warning("TS3 query failed: %s", exc)
            return f"TS3 查询失败：{exc}"
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected TS3 query error: %s", exc)
            return "TS3 查询失败：发生了未预期错误，请查看 AstrBot 日志。"

        return self._format_server_status(status)

    def _format_server_status(self, status: Ts3ServerStatus) -> str:
        lines = [
            f"服务器名称：{status.server_name or '-'}",
            f"服务器地址：{status.server_host}:{status.server_port}",
            f"在线人数：{status.online_count}",
            "频道信息：",
        ]

        for channel_name, users in self._group_users_by_channel(status):
            display_name = channel_name or "未命名频道"
            if users:
                lines.append(f"- {display_name}（{len(users)}人）：{'、'.join(users)}")
            else:
                lines.append(f"- {display_name}（0人）")

        return "\n".join(lines)

    def _group_users_by_channel(self, status: Ts3ServerStatus) -> list[tuple[str, list[str]]]:
        grouped: dict[str, list[str]] = {}
        for channel_name in status.channel_names:
            grouped.setdefault(channel_name, [])

        for user in status.users:
            grouped.setdefault(user.channel_name or "未命名频道", []).append(user.nickname)

        return list(grouped.items())

    def _get_missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        if not str(self.config.get("server_host", "")).strip():
            missing.append("服务器地址")
        if self._get_int_config("server_port", 0) <= 0:
            missing.append("服务器端口")
        if not str(self.config.get("serverquery_username", "")).strip():
            missing.append("ServerQuery 账号")
        if not str(self.config.get("serverquery_password", "")).strip():
            missing.append("ServerQuery 密码")
        return missing

    def _get_int_config(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_bool_config(self, key: str, default: bool = False) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return default

    async def _send_text_response(self, event: AstrMessageEvent, text: str) -> bool:
        target = getattr(event, "unified_msg_origin", "")
        if not target:
            return False

        try:
            await self.context.send_message(target, MessageChain().message(text))
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("TS3 response send failed, fallback to plain_result: %s", exc)
            return False
