from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    from .notifications import build_offline_message, build_online_message, format_duration
    from .presence import PresenceTracker
    from .storage import PluginLocalStorage
    from .ts3_query import Ts3QueryClient, Ts3QueryError
except ImportError:
    current_dir = Path(__file__).resolve().parent
    if str(current_dir) not in sys.path:
        sys.path.insert(0, str(current_dir))

    from notifications import build_offline_message, build_online_message, format_duration
    from presence import PresenceTracker
    from storage import PluginLocalStorage
    from ts3_query import Ts3QueryClient, Ts3QueryError


PLUGIN_NAME = "astrbot_plugin_ts3_tracker"


@register(
    "ts3_tracker",
    "Codex",
    "查询 TeamSpeak 3 服务器在线用户，并持续推送进入、离开和在线时长通知。",
    "1.0.1",
    "",
)
class Ts3TrackerPlugin(Star):
    STATUS_COMMANDS = {"ts", "上号", "人呢"}
    SERVER_COMMANDS = {"tsinfo", "ts服务器"}

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or {}
        self.storage = PluginLocalStorage(
            self._resolve_storage_dir(),
            legacy_base_dirs=[Path(__file__).resolve().parent / "data"],
        )
        self.presence_tracker = PresenceTracker(self.storage)
        self.monitor_task: Optional[asyncio.Task] = None
        self._recent_message_claims: dict[str, float] = {}
        self._ensure_monitor_task()
        logger.info("TS3 Tracker 插件已初始化，数据目录: %s", self.storage.base_dir)

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        self._ensure_monitor_task()
        self._debug_log("AstrBot 初始化完成，监控任务已检查启动状态")

    @filter.command("ts", alias={"上号", "人呢"})
    async def query_ts_status(self, event: AstrMessageEvent):
        """查询当前 TS3 在线用户。"""
        self._ensure_monitor_task()
        if not self._claim_message(event):
            return
        event.stop_event()
        message = await self._build_status_message()
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.command("tsinfo", alias={"ts服务器"})
    async def query_ts_server(self, event: AstrMessageEvent):
        """查询 TS3 服务器信息。"""
        self._ensure_monitor_task()
        if not self._claim_message(event):
            return
        event.stop_event()
        message = await self._build_server_info_message()
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("tsnotify", alias={"ts监听", "ts通知"})
    async def toggle_ts_notify(self, event: AstrMessageEvent, action: str = ""):
        """管理员控制当前会话是否接收 TS3 进入/离开通知。"""
        self._ensure_monitor_task()
        if not self._claim_message(event):
            return
        event.stop_event()

        target = getattr(event, "unified_msg_origin", "")
        current_enabled = self.storage.is_notify_target_enabled(target)
        normalized = action.strip().lower()

        if normalized in {"", "status", "状态"}:
            status_text = "已开启" if current_enabled else "未开启"
            message = (
                "当前会话的 TS3 通知监听状态：" + status_text + "\n"
                "使用 /tsnotify on 开启\n"
                "使用 /tsnotify off 关闭\n"
                "也可以使用 /tsbind 和 /tsunbind"
            )
            if await self._send_text_response(event, message):
                return
            yield event.plain_result(message)
            return

        if normalized in {"on", "enable", "enabled", "open", "true", "1", "开启", "开", "订阅", "绑定"}:
            changed = self.storage.add_notify_target(target)
            if changed:
                self._debug_log("Registered notify target by admin command: %s", target)
                message = "当前会话已开启 TS3 进入/离开通知监听。"
            else:
                message = "当前会话已经在监听 TS3 进入/离开通知。"
            if await self._send_text_response(event, message):
                return
            yield event.plain_result(message)
            return

        if normalized in {"off", "disable", "disabled", "close", "false", "0", "关闭", "关", "取消", "解绑"}:
            changed = self.storage.disable_notify_target(target)
            if changed:
                self._debug_log("Disabled notify target by admin command: %s", target)
                message = "当前会话已关闭 TS3 进入/离开通知监听。"
            else:
                message = "当前会话原本就没有开启 TS3 进入/离开通知监听。"
            if await self._send_text_response(event, message):
                return
            yield event.plain_result(message)
            return

        message = (
            "参数不正确。\n"
            "使用 /tsnotify on 开启\n"
            "使用 /tsnotify off 关闭\n"
            "使用 /tsnotify status 查看状态"
        )
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("tsbind", alias={"ts绑定", "ts开启监听"})
    async def bind_ts_notify(self, event: AstrMessageEvent):
        """管理员开启当前会话的 TS3 进入/离开通知监听。"""
        if not self._claim_message(event):
            return
        event.stop_event()
        changed = self.storage.add_notify_target(getattr(event, "unified_msg_origin", ""))
        message = (
            "当前会话已开启 TS3 进入/离开通知监听。"
            if changed
            else "当前会话已经在监听 TS3 进入/离开通知。"
        )
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("tsunbind", alias={"ts解绑", "ts关闭监听"})
    async def unbind_ts_notify(self, event: AstrMessageEvent):
        """管理员关闭当前会话的 TS3 进入/离开通知监听。"""
        if not self._claim_message(event):
            return
        event.stop_event()
        changed = self.storage.disable_notify_target(getattr(event, "unified_msg_origin", ""))
        message = (
            "当前会话已关闭 TS3 进入/离开通知监听。"
            if changed
            else "当前会话原本就没有开启 TS3 进入/离开通知监听。"
        )
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("tsdbclear", alias={"ts清库", "ts清空数据库"})
    async def clear_database(self, event: AstrMessageEvent, confirm: str = ""):
        """清空 TS3 Tracker 数据库。使用 /tsdbclear 确认 执行。"""
        if not self._claim_message(event):
            return
        event.stop_event()
        if confirm.strip().lower() not in {"确认", "confirm", "yes"}:
            yield event.plain_result(
                "这是危险操作，会清空在线历史、通知目标和监控基线。\n"
                "请使用 /tsdbclear 确认 执行。"
            )
            return

        self.storage.clear_database()
        self._debug_log("TS3 tracker database cleared by admin command")
        message = (
            "TS3 Tracker 数据库已清空。\n"
            "已重置：在线历史、通知目标、监控基线。\n"
            "监控开启时，下一个轮询周期会重新建立基线。"
        )
        if await self._send_text_response(event, message):
            return
        yield event.plain_result(message)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def query_ts_status_plain(self, event: AstrMessageEvent):
        """在启用无前缀触发时，响应 ts 和 tsserver 相关指令。"""
        if not self._allow_plain_text_trigger():
            return

        self._ensure_monitor_task()
        content = (event.message_str or "").strip()
        if content in self.STATUS_COMMANDS:
            if not self._claim_message(event):
                return
            event.stop_event()
            message = await self._build_status_message()
            if await self._send_text_response(event, message):
                return
            yield event.plain_result(message)
            return

        if content in self.SERVER_COMMANDS:
            if not self._claim_message(event):
                return
            event.stop_event()
            message = await self._build_server_info_message()
            if await self._send_text_response(event, message):
                return
            yield event.plain_result(message)

    async def _build_status_message(self) -> str:
        status = await self._fetch_status()
        if isinstance(status, str):
            return status

        channel_members = self._group_users_by_channel(status)
        if not channel_members:
            return "没有人。"

        lines = []
        for channel_name, nicknames in channel_members:
            lines.append(f"{channel_name}: {'、'.join(nicknames)}")
        return "\n".join(lines)

    async def _build_server_info_message(self) -> str:
        status = await self._fetch_status()
        if isinstance(status, str):
            return status

        lines = [
            f"服务器地址：{status.server_host}",
            f"服务器端口：{status.server_port}",
            f"服务器名称：{status.server_name or '-'}",
            "服务器频道：",
        ]
        channel_members = self._group_user_labels_by_channel(status)
        if status.channel_names:
            seen: set[str] = set()
            for channel_name in status.channel_names:
                user_labels = channel_members.get(channel_name, [])
                if user_labels:
                    lines.append(f"{channel_name}: {'、'.join(user_labels)}")
                else:
                    lines.append(channel_name)
                seen.add(channel_name)
            for channel_name, user_labels in channel_members.items():
                if channel_name not in seen:
                    lines.append(f"{channel_name}: {'、'.join(user_labels)}")
        else:
            lines.append("-")
        return "\n".join(lines)

    async def _monitor_loop(self) -> None:
        while True:
            try:
                if not self._monitor_enabled():
                    await asyncio.sleep(2)
                    continue

                status = await self._fetch_status()
                if isinstance(status, str):
                    await asyncio.sleep(self._monitor_interval_seconds())
                    continue

                events = self.presence_tracker.reconcile(status, time.time())
                if events:
                    await self._dispatch_presence_events(events)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.exception("TS3 monitor loop failed: %s", exc)

            await asyncio.sleep(self._monitor_interval_seconds())

    async def _dispatch_presence_events(self, events) -> None:
        targets = self.storage.load_notify_targets()
        if not targets:
            self._debug_log("Presence events detected but no notify targets are bound yet")
            return

        for event in events:
            if event.kind == "online":
                message = build_online_message(
                    nickname=event.nickname,
                    timestamp=event.start_ts,
                    total_users=event.total_users,
                    online_names=event.online_names,
                )
            else:
                message = build_offline_message(
                    nickname=event.nickname,
                    start_ts=event.start_ts,
                    end_ts=event.end_ts or event.start_ts,
                    online_names=event.online_names,
                )

            for target in targets:
                try:
                    await self.context.send_message(target, MessageChain().message(message))
                    self.storage.mark_notify_target_success(target)
                except Exception as exc:  # pragma: no cover - platform dependent
                    self.storage.mark_notify_target_error(target, str(exc))
                    logger.warning("TS3 notification send failed to %s: %s", target, exc)

            self._debug_log(
                "Presence notification dispatched: %s - %s",
                event.nickname,
                event.kind,
            )

    async def _fetch_status(self):
        missing_fields = self._get_missing_required_fields()
        if missing_fields:
            return "TS3 配置不完整，请先在插件配置页填写：" + "、".join(missing_fields)

        host = str(self.config.get("server_host", "")).strip()
        server_port = self._get_int_config("server_port", 0)
        query_port = self._get_int_config("serverquery_port", 10011)
        username = str(self.config.get("serverquery_username", "")).strip()
        password = str(self.config.get("serverquery_password", "")).strip()

        client = Ts3QueryClient(
            host=host,
            server_port=server_port,
            query_port=query_port,
            username=username,
            password=password,
            timeout=10.0,
            debug=self._debug_enabled(),
        )

        self._debug_log(
            "Preparing TS3 status query host=%s server_port=%s query_port=%s user=%s",
            host,
            server_port,
            query_port,
            username,
        )

        try:
            status = await client.fetch_status()
        except Ts3QueryError as exc:
            logger.warning("TS3 query failed: %s", exc)
            return f"TS3 查询失败：{exc}"
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected TS3 query error: %s", exc)
            return "TS3 查询失败：发生了未预期的错误，请打开 debug 后查看日志。"

        self.storage.save_last_status(status.to_dict())
        self._debug_log("TS3 query succeeded with %s online clients", status.online_count)
        return status

    def _group_users_by_channel(self, status) -> list[tuple[str, list[str]]]:
        grouped: dict[str, list[str]] = {}
        for user in status.users:
            channel_name = user.channel_name or "未知频道"
            grouped.setdefault(channel_name, []).append(user.nickname)

        ordered: list[tuple[str, list[str]]] = []
        seen: set[str] = set()
        for channel_name in status.channel_names:
            if channel_name in grouped:
                ordered.append((channel_name, grouped[channel_name]))
                seen.add(channel_name)

        for channel_name, nicknames in grouped.items():
            if channel_name not in seen:
                ordered.append((channel_name, nicknames))

        return ordered

    def _group_user_labels_by_channel(self, status) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for user in status.users:
            channel_name = user.channel_name or "未知频道"
            duration = format_duration(int(getattr(user, "connected_duration_seconds", 0)))
            grouped.setdefault(channel_name, []).append(f"{user.nickname}({duration})")
        return grouped

    def _resolve_storage_dir(self) -> Path:
        try:
            return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        except Exception:
            return Path(__file__).resolve().parent / "data"

    def _get_missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        if not str(self.config.get("server_host", "")).strip():
            missing.append("服务器 IP")
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
            if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "n", "off", "disabled"}:
                return False
        return default

    def _allow_plain_text_trigger(self) -> bool:
        return self._get_bool_config("enable_plain_text_trigger", False)

    def _monitor_enabled(self) -> bool:
        return self._get_bool_config("enable_monitor", False)

    def _monitor_interval_seconds(self) -> int:
        interval = self._get_int_config("monitor_interval_seconds", 5)
        return max(5, interval)

    def _debug_enabled(self) -> bool:
        return self._get_bool_config("debug", False)

    def _ensure_monitor_task(self) -> None:
        if self.monitor_task and not self.monitor_task.done():
            return

        if self.monitor_task and self.monitor_task.done():
            try:
                exc = self.monitor_task.exception()
            except asyncio.CancelledError:
                exc = None
            if exc:
                logger.warning("TS3 monitor task exited with error, restarting: %s", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        self.monitor_task = loop.create_task(self._monitor_loop())
        self._debug_log("TS3 monitor task started")

    def _claim_message(self, event: AstrMessageEvent) -> bool:
        now = time.monotonic()
        expired_keys = [
            key for key, ts in self._recent_message_claims.items() if now - ts > 15
        ]
        for key in expired_keys:
            self._recent_message_claims.pop(key, None)

        message_obj = getattr(event, "message_obj", None)
        message_id = getattr(message_obj, "message_id", "") if message_obj else ""
        session_id = getattr(message_obj, "session_id", "") if message_obj else ""
        timestamp = getattr(message_obj, "timestamp", "") if message_obj else ""
        content = (event.message_str or "").strip()
        claim_key = "|".join(
            [
                str(getattr(event, "unified_msg_origin", "")),
                str(session_id),
                str(message_id),
                str(timestamp),
                content,
            ]
        )

        if self._recent_message_claims.get(claim_key):
            self._debug_log("Duplicate message ignored: %s", claim_key)
            return False

        self._recent_message_claims[claim_key] = now
        return True

    def _debug_log(self, message: str, *args) -> None:
        if self._debug_enabled():
            logger.info("[TS3 Tracker] " + message, *args)

    async def _send_text_response(self, event: AstrMessageEvent, text: str) -> bool:
        target = getattr(event, "unified_msg_origin", "")
        if not target:
            return False

        try:
            await self.context.send_message(target, MessageChain().message(text))
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            self._debug_log("Direct response send failed, fallback to plain_result: %s", exc)
            return False

    async def terminate(self):
        if self.monitor_task:
            self.monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor_task
