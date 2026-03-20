from __future__ import annotations

import time


def format_timestamp(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def format_duration(duration_seconds: int) -> str:
    seconds = max(0, int(duration_seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


def build_online_message(
    nickname: str,
    timestamp: float,
    total_users: int,
    online_names: list[str],
) -> str:
    return (
        "让我看看是谁还没上号 👀\n"
        f"🧾 昵称：{nickname}\n"
        f"🟢 上线时间：{format_timestamp(timestamp)}\n"
        f"📣 {nickname} 进入了 TS 服务器\n"
        f"👥 当前在线人数：{total_users}\n"
        f"📜 在线列表：{', '.join(online_names) if online_names else '（无在线用户）'}"
    )


def build_offline_message(
    nickname: str,
    start_ts: float,
    end_ts: float,
    online_names: list[str],
) -> str:
    duration = int(end_ts - start_ts)
    return (
        "📤 用户下线通知\n"
        f"🧾 昵称：{nickname}\n"
        f"🟢 上线时间：{format_timestamp(start_ts)}\n"
        f"🔴 下线时间：{format_timestamp(end_ts)}\n"
        f"⏱️ 在线时长：{format_duration(duration)}\n"
        f"👥 当前在线人数：{len(online_names)}\n"
        f"📜 在线列表：{', '.join(online_names) if online_names else '（无在线用户）'}"
    )
