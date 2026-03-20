from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from pathlib import Path
import time
from typing import Any

from notifications import build_offline_message, build_online_message, format_duration
from presence import PresenceTracker, build_server_key
from storage import PluginLocalStorage
from ts3_query import Ts3QueryClient, Ts3QueryError


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "data" / "ts3_cli_config.json"
CLI_MONITOR_STATE_DIR = SCRIPT_DIR / "data" / "cli_monitor_state"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="独立的 TS3 ServerQuery 测试脚本。",
    )
    subparsers = parser.add_subparsers(dest="command")

    query_parser = subparsers.add_parser("query", help="执行一次 TS3 在线用户查询")
    _add_connection_args(query_parser)
    query_parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果",
    )

    serverinfo_parser = subparsers.add_parser("serverinfo", help="显示服务器基础信息、频道列表和在线时长")
    _add_connection_args(serverinfo_parser)
    serverinfo_parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果",
    )

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="持续检测进入/离开并在终端打印通知",
    )
    _add_connection_args(monitor_parser)
    monitor_parser.add_argument(
        "--interval",
        default=5,
        type=int,
        help="轮询间隔秒数，默认 5。过低可能触发服务器黑名单。",
    )
    monitor_parser.add_argument(
        "--iterations",
        default=0,
        type=int,
        help="轮询次数，0 表示持续运行",
    )
    monitor_parser.add_argument(
        "--reset-state",
        action="store_true",
        help="清空本地监控基线后再开始监控",
    )

    serverlist_parser = subparsers.add_parser(
        "serverlist",
        help="登录 ServerQuery 并列出可用虚拟服务器",
    )
    serverlist_parser.add_argument("--host", required=True, help="TS3 服务器 IP 或域名")
    serverlist_parser.add_argument(
        "--query-port",
        default=10011,
        type=int,
        help="ServerQuery 端口，默认 10011",
    )
    serverlist_parser.add_argument("--username", required=True, help="ServerQuery 用户名")
    serverlist_parser.add_argument("--password", help="ServerQuery 密码，不传则会安全提示输入")
    serverlist_parser.add_argument(
        "--timeout",
        default=10.0,
        type=float,
        help="网络超时秒数，默认 10",
    )

    shell_parser = subparsers.add_parser("shell", help="进入交互式测试终端")
    shell_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"配置文件路径，默认是 {DEFAULT_CONFIG_PATH}",
    )

    return parser


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="TS3 服务器 IP 或域名")
    parser.add_argument("--server-port", required=True, type=int, help="TS3 虚拟服务器端口")
    parser.add_argument(
        "--query-port",
        default=10011,
        type=int,
        help="ServerQuery 端口，默认 10011",
    )
    parser.add_argument("--username", required=True, help="ServerQuery 用户名")
    parser.add_argument("--password", help="ServerQuery 密码，不传则会安全提示输入")
    parser.add_argument(
        "--timeout",
        default=10.0,
        type=float,
        help="网络超时秒数，默认 10",
    )


async def run_single_query(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("ServerQuery 密码: ")
    client = Ts3QueryClient(
        host=args.host,
        server_port=args.server_port,
        query_port=args.query_port,
        username=args.username,
        password=password,
        timeout=args.timeout,
        debug=False,
    )

    try:
        status = await client.fetch_status()
    except Ts3QueryError as exc:
        print(f"查询失败: {exc}")
        return 1

    if args.json:
        print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_status(status.to_dict()))
    return 0


async def run_serverinfo(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("ServerQuery 密码: ")
    client = Ts3QueryClient(
        host=args.host,
        server_port=args.server_port,
        query_port=args.query_port,
        username=args.username,
        password=password,
        timeout=args.timeout,
        debug=False,
    )

    try:
        status = await client.fetch_status()
    except Ts3QueryError as exc:
        print(f"查询失败: {exc}")
        return 1

    if args.json:
        print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_server_info(status.to_dict()))
    return 0


async def run_monitor(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("ServerQuery 密码: ")
    storage = PluginLocalStorage(CLI_MONITOR_STATE_DIR)
    server_key = build_server_key(args.host, args.server_port)
    if args.reset_state:
        storage.reset_runtime_state(server_key)

    tracker = PresenceTracker(storage)
    iterations = int(args.iterations)
    poll_interval = max(1, int(args.interval))

    print("监控已启动。")
    print("提示：轮询过于频繁可能会被 TS3 服务器拉入黑名单。")

    count = 0
    while True:
        baseline_initialized = storage.is_baseline_initialized(server_key)
        client = Ts3QueryClient(
            host=args.host,
            server_port=args.server_port,
            query_port=args.query_port,
            username=args.username,
            password=password,
            timeout=args.timeout,
            debug=False,
        )

        try:
            status = await client.fetch_status()
        except Ts3QueryError as exc:
            print(f"查询失败: {exc}")
        else:
            events = tracker.reconcile(status, time.time())
            if not baseline_initialized:
                print("已建立监控基线，当前在线用户不会补发上线通知。")
            for event in events:
                if event.kind == "online":
                    print(
                        build_online_message(
                            nickname=event.nickname,
                            timestamp=event.start_ts,
                            total_users=event.total_users,
                            online_names=event.online_names,
                        )
                    )
                else:
                    print(
                        build_offline_message(
                            nickname=event.nickname,
                            start_ts=event.start_ts,
                            end_ts=event.end_ts or event.start_ts,
                            online_names=event.online_names,
                        )
                    )
                print("-" * 20)

        count += 1
        if iterations > 0 and count >= iterations:
            return 0
        await asyncio.sleep(poll_interval)


async def run_serverlist(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("ServerQuery 密码: ")
    client = Ts3QueryClient(
        host=args.host,
        server_port=9987,
        query_port=args.query_port,
        username=args.username,
        password=password,
        timeout=args.timeout,
        debug=False,
    )

    try:
        servers = await client.list_virtual_servers()
    except Ts3QueryError as exc:
        print(f"查询失败: {exc}")
        return 1

    if not servers:
        print("没有返回任何虚拟服务器。")
        return 0

    print("可用虚拟服务器:")
    for index, server in enumerate(servers, start=1):
        sid = server.get("virtualserver_id", "-")
        port = server.get("virtualserver_port", "-")
        name = server.get("virtualserver_name", "-")
        clients = server.get("virtualserver_clientsonline", "-")
        status = server.get("virtualserver_status", "-")
        print(
            f"{index}. sid={sid} port={port} clients={clients} status={status} name={name}"
        )
    return 0


def format_status(payload: dict[str, Any]) -> str:
    grouped: dict[str, list[str]] = {}
    for user in payload.get("users", []):
        channel_name = user.get("channel_name") or "未知频道"
        grouped.setdefault(channel_name, []).append(user.get("nickname", "-"))

    if not grouped:
        return "没有人。"

    lines = []
    seen: set[str] = set()
    for channel_name in payload.get("channel_names", []):
        if channel_name in grouped:
            lines.append(f"{channel_name}: {'、'.join(grouped[channel_name])}")
            seen.add(channel_name)

    for channel_name, nicknames in grouped.items():
        if channel_name not in seen:
            lines.append(f"{channel_name}: {'、'.join(nicknames)}")
    return "\n".join(lines)


def format_server_info(payload: dict[str, Any]) -> str:
    lines = [
        f"服务器地址：{payload.get('server_host') or '-'}",
        f"服务器端口：{payload.get('server_port') or '-'}",
        f"服务器名称：{payload.get('server_name') or '-'}",
        "服务器频道：",
    ]
    grouped: dict[str, list[str]] = {}
    for user in payload.get("users", []):
        channel_name = user.get("channel_name") or "未知频道"
        duration = format_duration(int(user.get("connected_duration_seconds", 0) or 0))
        grouped.setdefault(channel_name, []).append(f"{user.get('nickname', '-') }({duration})")
    channel_names = payload.get("channel_names", [])
    if channel_names:
        seen: set[str] = set()
        for channel_name in channel_names:
            user_labels = grouped.get(channel_name, [])
            if user_labels:
                lines.append(f"{channel_name}: {'、'.join(user_labels)}")
            else:
                lines.append(channel_name)
            seen.add(channel_name)
        for channel_name, user_labels in grouped.items():
            if channel_name not in seen:
                lines.append(f"{channel_name}: {'、'.join(user_labels)}")
    else:
        lines.append("-")
    return "\n".join(lines)


class InteractiveShell:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.state: dict[str, Any] = {
            "host": "",
            "server_port": 9987,
            "query_port": 10011,
            "username": "",
            "password": "",
            "timeout": 10.0,
        }
        self.load(silent=True)

    def load(self, silent: bool = False) -> None:
        if not self.config_path.exists():
            if not silent:
                print(f"配置文件不存在: {self.config_path}")
            return
        self.state.update(json.loads(self.config_path.read_text(encoding="utf-8")))
        if not silent:
            print(f"已加载配置: {self.config_path}")

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已保存配置: {self.config_path}")

    def show(self) -> None:
        masked = dict(self.state)
        masked["password"] = "***" if masked.get("password") else ""
        print(json.dumps(masked, ensure_ascii=False, indent=2))

    async def query(self) -> None:
        missing = [
            key
            for key in ("host", "server_port", "username", "password")
            if not self.state.get(key)
        ]
        if missing:
            print("配置不完整，缺少: " + ", ".join(missing))
            return

        client = Ts3QueryClient(
            host=str(self.state["host"]),
            server_port=int(self.state["server_port"]),
            query_port=int(self.state["query_port"]),
            username=str(self.state["username"]),
            password=str(self.state["password"]),
            timeout=float(self.state["timeout"]),
            debug=False,
        )

        try:
            status = await client.fetch_status()
        except Ts3QueryError as exc:
            print(f"查询失败: {exc}")
            return

        print(format_status(status.to_dict()))

    async def serverinfo(self) -> None:
        missing = [
            key
            for key in ("host", "server_port", "username", "password")
            if not self.state.get(key)
        ]
        if missing:
            print("配置不完整，缺少: " + ", ".join(missing))
            return

        client = Ts3QueryClient(
            host=str(self.state["host"]),
            server_port=int(self.state["server_port"]),
            query_port=int(self.state["query_port"]),
            username=str(self.state["username"]),
            password=str(self.state["password"]),
            timeout=float(self.state["timeout"]),
            debug=False,
        )

        try:
            status = await client.fetch_status()
        except Ts3QueryError as exc:
            print(f"查询失败: {exc}")
            return

        print(format_server_info(status.to_dict()))

    async def serverlist(self) -> None:
        missing = [
            key
            for key in ("host", "username", "password")
            if not self.state.get(key)
        ]
        if missing:
            print("配置不完整，缺少: " + ", ".join(missing))
            return

        client = Ts3QueryClient(
            host=str(self.state["host"]),
            server_port=int(self.state["server_port"]),
            query_port=int(self.state["query_port"]),
            username=str(self.state["username"]),
            password=str(self.state["password"]),
            timeout=float(self.state["timeout"]),
            debug=False,
        )

        try:
            servers = await client.list_virtual_servers()
        except Ts3QueryError as exc:
            print(f"查询失败: {exc}")
            return

        if not servers:
            print("没有返回任何虚拟服务器。")
            return

        for index, server in enumerate(servers, start=1):
            sid = server.get("virtualserver_id", "-")
            port = server.get("virtualserver_port", "-")
            name = server.get("virtualserver_name", "-")
            clients = server.get("virtualserver_clientsonline", "-")
            status = server.get("virtualserver_status", "-")
            print(
                f"{index}. sid={sid} port={port} clients={clients} status={status} name={name}"
            )

    def set_value(self, key: str, value: str) -> None:
        if key not in self.state:
            print(f"未知字段: {key}")
            return
        if key in {"server_port", "query_port"}:
            self.state[key] = int(value)
        elif key == "timeout":
            self.state[key] = float(value)
        else:
            self.state[key] = value
        print(f"已设置 {key}")

    @staticmethod
    def print_help() -> None:
        print(
            "\n".join(
                [
                    "可用命令:",
                    "  show                         查看当前配置",
                    "  set host 1.2.3.4             设置服务器地址",
                    "  set server_port 9987         设置 TS3 服务器端口",
                    "  set query_port 10011         设置 ServerQuery 端口",
                    "  set username serveradmin     设置 ServerQuery 用户名",
                    "  set password 你的密码         设置 ServerQuery 密码",
                    "  set timeout 10               设置超时秒数",
                    "  load                         从配置文件重新加载",
                    "  save                         保存当前配置",
                    "  serverlist                   列出可用虚拟服务器",
                    "  serverinfo                   查看服务器信息和频道列表",
                    "  query                        立即查询在线用户",
                    "  help                         查看帮助",
                    "  exit                         退出",
                ]
            )
        )

    async def run(self) -> int:
        print("进入 TS3 测试终端。输入 help 查看命令。")
        while True:
            try:
                raw = input("ts3> ").strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                return 0

            if not raw:
                continue

            if raw in {"exit", "quit"}:
                return 0
            if raw == "help":
                self.print_help()
                continue
            if raw == "show":
                self.show()
                continue
            if raw == "load":
                self.load()
                continue
            if raw == "save":
                self.save()
                continue
            if raw == "serverlist":
                await self.serverlist()
                continue
            if raw == "serverinfo":
                await self.serverinfo()
                continue
            if raw == "query":
                await self.query()
                continue
            if raw.startswith("set "):
                parts = raw.split(" ", 2)
                if len(parts) != 3:
                    print("用法: set <字段> <值>")
                    continue
                _, key, value = parts
                try:
                    self.set_value(key, value)
                except ValueError as exc:
                    print(f"设置失败: {exc}")
                continue

            print("未知命令，输入 help 查看可用命令。")


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "query":
        return await run_single_query(args)

    if args.command == "serverinfo":
        return await run_serverinfo(args)

    if args.command == "monitor":
        return await run_monitor(args)

    if args.command == "serverlist":
        return await run_serverlist(args)

    if args.command == "shell":
        shell = InteractiveShell(Path(args.config))
        return await shell.run()

    parser.print_help()
    return 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
