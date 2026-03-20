from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass
from typing import Any


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
            await self._execute(
                reader,
                writer,
                f"use port={self.server_port}",
                "use",
            )
            serverinfo_records = await self._execute(reader, writer, "serverinfo", "serverinfo")
            channel_records = await self._execute(
                reader,
                writer,
                "channellist",
                "channellist",
            )
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
                clid = client.get("clid", "")
                if not clid:
                    continue
                detail_records = await self._execute(
                    reader,
                    writer,
                    f"clientinfo clid={clid}",
                    "clientinfo",
                )
                client_details[clid] = detail_records[0] if detail_records else {}
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
            channel_name
            for channel_name in (channel.get("channel_name", "") for channel in channel_records)
            if channel_name
        ]

        users: list[Ts3OnlineUser] = []
        for client in client_records:
            if client.get("client_type") == "1":
                continue

            user = Ts3OnlineUser(
                nickname=client.get("client_nickname", ""),
                channel_name=channels.get(client.get("cid", ""), ""),
                client_id=client.get("clid", ""),
                database_id=client.get("client_database_id", ""),
                unique_id=client.get("client_unique_identifier", ""),
                client_ip=client.get("connection_client_ip", ""),
                connected_duration_seconds=max(
                    0,
                    int(
                        client_details.get(client.get("clid", ""), {}).get(
                            "connection_connected_time",
                            "0",
                        )
                        or "0"
                    )
                    // 1000,
                ),
                away=client.get("client_away", "0") == "1",
            )
            users.append(user)

        users.sort(key=lambda item: item.nickname.casefold())

        return Ts3ServerStatus(
            server_name=serverinfo.get("virtualserver_name", ""),
            server_host=self.host,
            server_port=int(serverinfo.get("virtualserver_port", self.server_port)),
            online_count=len(users),
            channel_names=channel_names,
            users=users,
        )

    async def list_virtual_servers(self) -> list[dict[str, str]]:
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
            servers = await self._execute(
                reader,
                writer,
                "serverlist -uid",
                "serverlist",
            )
            await self._write_line(writer, "quit")
            return servers
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

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
        # A TeamSpeak ServerQuery connection usually starts with a short banner,
        # not a regular command response. Consume it before the first command.
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
            if not raw_record:
                continue
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
