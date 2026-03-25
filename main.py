from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
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
    channel_id: str
    channel_name: str
    client_id: str
    database_id: str
    unique_id: str
    client_ip: str
    connected_duration_seconds: int
    away: bool


@dataclass
class Ts3ServerStatus:
    server_name: str
    server_host: str
    server_port: int
    online_count: int
    channels: list[tuple[str, str]]
    users: list[Ts3OnlineUser]


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
            async with asyncio.timeout(self.timeout):
                return await self._fetch_status_inner()
        except TimeoutError as exc:
            raise Ts3QueryError(f"TS3 查询超时（超过 {self.timeout:.0f} 秒）") from exc

    async def _fetch_status_inner(self) -> Ts3ServerStatus:
        try:
            reader, writer = await asyncio.open_connection(self.host, self.query_port)
        except Exception as exc:  # pragma: no cover - network dependent
            raise Ts3QueryError(
                f"无法连接到 ServerQuery：{self.host}:{self.query_port} ({exc})"
            ) from exc

        try:
            await self._consume_welcome(reader)
            # ServerQuery 的 login 请求是明文传输。建议只在 localhost、内网
            # 或通过 SSH/VPN 等受保护隧道访问时使用，避免凭据暴露在公网链路。
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
                "clientlist -uid -away -ip -times",
                "clientlist",
            )
            await self._write_line(writer, "quit")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        serverinfo = serverinfo_records[0] if serverinfo_records else {}
        channel_map = {
            channel.get("cid", ""): channel.get("channel_name", "")
            for channel in channel_records
            if channel.get("cid", "")
        }
        channel_order = [
            (channel.get("cid", ""), channel.get("channel_name", ""))
            for channel in channel_records
            if channel.get("cid", "")
        ]

        users: list[Ts3OnlineUser] = []
        for client in client_records:
            if client.get("client_type") == "1":
                continue

            users.append(
                Ts3OnlineUser(
                    nickname=client.get("client_nickname", ""),
                    channel_id=client.get("cid", ""),
                    channel_name=channel_map.get(client.get("cid", ""), ""),
                    client_id=client.get("clid", ""),
                    database_id=client.get("client_database_id", ""),
                    unique_id=client.get("client_unique_identifier", ""),
                    client_ip=client.get("connection_client_ip", ""),
                    connected_duration_seconds=max(
                        0,
                        self._safe_int(client.get("connection_connected_time"), 0) // 1000,
                    ),
                    away=client.get("client_away", "0") == "1",
                )
            )

        users.sort(key=lambda item: item.nickname.casefold())

        return Ts3ServerStatus(
            server_name=serverinfo.get("virtualserver_name", ""),
            server_host=self.host,
            server_port=self._safe_int(serverinfo.get("virtualserver_port"), self.server_port),
            online_count=len(users),
            channels=channel_order,
            users=users,
        )

    async def _execute(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str,
        action: str,
    ) -> list[dict[str, str]]:
        self._debug_log("ServerQuery -> %s", command)
        await self._write_line(writer, command)
        lines = await self._read_response(reader)
        return self._parse_response(lines, action)

    async def _write_line(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write(f"{line}\n".encode("utf-8"))
        await writer.drain()

    async def _consume_welcome(self, reader: asyncio.StreamReader) -> None:
        while True:
            raw_line = await reader.readline()
            if not raw_line:
                raise Ts3QueryError("ServerQuery 握手失败：欢迎信息意外结束")

            line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue

            self._debug_log("ServerQuery welcome: %s", line)

            if "TeamSpeak 3 ServerQuery interface" in line:
                return

    async def _read_response(self, reader: asyncio.StreamReader) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                raw_line = await reader.readline()
            except Exception as exc:
                raise Ts3QueryError(f"ServerQuery 读取响应失败：{exc}") from exc

            if not raw_line:
                if lines:
                    return lines
                raise Ts3QueryError("ServerQuery 连接已关闭")

            line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue

            self._debug_log("ServerQuery <- %s", line)
            lines.append(line)

            if line.startswith("error "):
                return lines

    def _parse_response(self, lines: list[str], action: str) -> list[dict[str, str]]:
        if not lines:
            return []

        error_line = lines[-1]
        if not error_line.startswith("error "):
            raise Ts3QueryError(f"{action} 失败：响应格式异常，缺少 error 行")

        error_info = self._parse_record(error_line.removeprefix("error "))
        try:
            error_id = int(error_info.get("id", "-1"))
        except (TypeError, ValueError) as exc:
            raise Ts3QueryError(f"{action} 失败：响应格式异常，error id 无法解析") from exc

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

    def _safe_int(self, value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _debug_log(self, message: str, *args) -> None:
        if self.debug:
            logger.info("[TS3 Query] " + message, *args)


@register(
    "ts3_tracker",
    "moeneri",
    "AstrBot TS3 查询插件，发送上号指令即可返回服务器在线信息。",
    "1.0.2",
    "https://github.com/moeneri/astrbot_plugin_ts3_tracker",
)
class Ts3TrackerPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or {}

    @filter.command("上号", alias={"ts", "tsinfo"})
    async def query_server_status(self, event: AstrMessageEvent):
        """查询 TS3 服务器在线信息。"""
        message = await self._build_server_message()
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
        grouped: dict[str, dict[str, object]] = {}
        for channel_id, channel_name in status.channels:
            grouped.setdefault(channel_id, {"name": channel_name, "users": []})

        for user in status.users:
            channel_id = user.channel_id or "__unknown__"
            channel_entry = grouped.setdefault(
                channel_id,
                {"name": user.channel_name or "未命名频道", "users": []},
            )
            user_label = user.nickname
            if self._get_bool_config("show_online_duration", False):
                user_label = f"{user.nickname}({self._format_duration(user.connected_duration_seconds)})"
            channel_entry["users"].append(user_label)

        ordered_groups: list[tuple[str, list[str]]] = []
        for channel_id, _channel_name in status.channels:
            channel_entry = grouped.get(channel_id)
            if channel_entry is None:
                continue
            ordered_groups.append((str(channel_entry["name"]), list(channel_entry["users"])))
            grouped.pop(channel_id, None)

        for channel_entry in grouped.values():
            ordered_groups.append((str(channel_entry["name"]), list(channel_entry["users"])))

        return ordered_groups

    def _format_duration(self, seconds: int) -> str:
        total_seconds = max(0, int(seconds))
        if total_seconds < 60:
            return "1分钟内"

        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes}分钟"

        hours, remaining_minutes = divmod(minutes, 60)
        if hours < 24:
            if remaining_minutes:
                return f"{hours}小时{remaining_minutes}分钟"
            return f"{hours}小时"

        days, remaining_hours = divmod(hours, 24)
        if remaining_hours:
            return f"{days}天{remaining_hours}小时"
        return f"{days}天"

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
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "off", "disabled"}:
                return False
        return default
