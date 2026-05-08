# AstrBot TeamSpeak 3 查询与通知插件

这是一个给 AstrBot 使用的 TeamSpeak 3 插件，可以让机器人查询当前 TS3 服务器在线情况，并在开启监听后推送成员上线、离线通知。

适合这些场景：

- 在 QQ 或其他已接入平台里快速查看 TS3 现在谁在线
- 让群聊持续接收 TS3 成员上线、离线提醒
- 用尽量简单的方式完成 TS3 基础查询和在线监控

## 功能介绍

本插件支持以下功能：

- 查询当前 TS3 在线用户
- 查询 TS3 服务器名称、地址、端口和频道信息
- 按频道展示在线成员
- 可选在在线查询中显示在线时长
- 可选限制只有指定群可以触发插件命令
- 支持自定义进服提示和退服提示模板
- 开启后持续监听成员上线、离线
- 向已绑定的会话推送通知消息

## 运行要求

- AstrBot `>=4.16,<5`
- 机器人运行环境可以连接到 TeamSpeak 3 的 ServerQuery 端口
- 已拥有可用的 ServerQuery 账号和密码

## 安装方式

在插件市场搜索TS3 或者 teamspeak 3 找到本插件安装即可

如果你使用 GitHub 管理插件，也可以把本项目上传到自己的仓库后，再通过 AstrBot 的插件管理方式安装。

## 配置说明

请在 AstrBot 插件配置页中填写以下项目。

### 基础配置

`server_host`

TS3 服务器地址。

示例：

- `ts.example.com`
- `127.0.0.1`

`server_port`

TS3 语音服务器端口，常见为：

- `9987`

`serverquery_port`

TS3 ServerQuery 端口，常见为：

- `10011`

`serveradmin`

ServerQuery 登录账号。

`passowrd`

ServerQuery 登录密码。

### 可选配置

`enable_plain_text_trigger`

是否允许不带 `/` 的纯文本触发。

关闭时，建议使用 `/ts`、`/tsinfo` 这类命令。

开启后，直接发送以下文本也可以触发查询：

- `ts`
- `上号`
- `人呢`
- `tsinfo`
- `ts服务器`

`show_online_duration_in_status`

是否在 `上号`、`/ts`、`人呢` 这类在线查询结果里显示在线时长。

关闭时示例：

```text
APEX:
test
原神:
玩家A
玩家B
```

开启时示例：

```text
APEX:
test(23分钟)
原神:
玩家A(1小时5分钟)
玩家B(12分钟)
```

默认关闭。

`enable_group_whitelist`

是否开启群白名单。

默认关闭。关闭时，所有群都可以触发插件命令。

开启后，只有白名单内的群才可以触发插件命令，私聊不受影响。

`group_whitelist`

群白名单内容。

只有在开启 `enable_group_whitelist` 后才会生效。

如果开启了群白名单但没有填写群号，那么所有群消息都会被拦截。

填写方式支持：

- 一行一个群号
- 使用英文逗号分隔
- 使用中文逗号分隔

示例：

```text
123456789
987654321
```

或者：

```text
123456789,987654321
```

`online_message_template`

进服提示模板。

配置页会默认预填当前插件原本使用的进服提示格式，你可以直接在这个基础上修改。

配置页中默认预填的内容如下：

```text
让我看看是谁还没上号 👀\n🧾 昵称：{nickname}\n🟢 上线时间：{time}\n📣 {nickname} 进入了 TS 服务器\n👥 当前在线人数：{total_users}\n📜 在线列表：{online_list}
```

说明：

- 可以直接保留这段原始内容使用
- 可以在此基础上修改文字
- 可以继续使用 `\n` 表示换行

支持变量：

- `{nickname}` 或 `{username}`：用户名
- `{time}`、`{timestamp}`、`{start_time}`、`{online_time}`：上线时间
- `{total_users}` 或 `{online_count}`：当前在线人数
- `{online_list}`：当前在线列表

`offline_message_template`

退服提示模板。

配置页会默认预填当前插件原本使用的退服提示格式，你可以直接在这个基础上修改。

配置页中默认预填的内容如下：

```text
📤 用户下线通知\n🧾 昵称：{nickname}\n🟢 上线时间：{start_time}\n🔴 下线时间：{end_time}\n⏱️ 在线时长：{duration}\n👥 当前在线人数：{online_count}\n📜 在线列表：{online_list}
```

说明：

- 可以直接保留这段原始内容使用
- 可以在此基础上修改文字
- 可以继续使用 `\n` 表示换行

支持变量：

- `{nickname}` 或 `{username}`：用户名
- `{time}`、`{timestamp}`、`{end_time}`、`{offline_time}`：下线时间
- `{start_time}`：上线时间
- `{duration}`：在线时长
- `{total_users}` 或 `{online_count}`：当前在线人数
- `{online_list}`：当前在线列表

`enable_monitor`

是否开启上线、离线监听功能。

开启后，插件会持续轮询 TS3 在线列表，并向已绑定通知的会话推送提醒。

`monitor_interval_seconds`

监听轮询间隔，单位为秒。

建议不要设置过低。默认值为 `5`，插件也会强制不低于 `5` 秒。

`debug`

是否输出更详细的调试日志。

如果你遇到连接失败、查询异常、通知不发送等问题，可以先开启这个选项再查看 AstrBot 日志。

## 使用方法

### 普通查询命令

查询当前在线情况：

- `/ts`
- `/上号`
- `/人呢`

查询服务器信息：

- `/tsinfo`
- `/ts服务器`

如果你开启了 `enable_plain_text_trigger`，也可以直接发送上面的文字而不加 `/`。

### 管理员命令

以下命令通常需要管理员权限。

查看当前会话的通知状态：

- `/tsnotify`
- `/tsnotify status`

开启当前会话的 TS3 通知：

- `/tsnotify on`
- `/tsbind`

关闭当前会话的 TS3 通知：

- `/tsnotify off`
- `/tsunbind`

清空插件本地记录：

- `/tsdbclear 确认`

## 返回示例

### 在线查询示例

```text
APEX:
test
原神:
玩家A
玩家B
```

开启在线时长显示后：

```text
APEX:
test(23分钟)
原神:
玩家A(1小时5分钟)
玩家B(12分钟)
```

如果当前没有人在线，插件会返回：

```text
没有人。
```

### 服务器信息示例

```text
服务器名称：东雪莲粉丝俱乐部
服务器地址：127.0.0.1:9987
在线人数：1
频道信息：
- APEX（1人）：test(15分钟32秒)
- 原神（0人）
- 守望先锋-归西（0人）
- 穿越火线（0人）
- 永雏塔菲（0人）
- 高能英雄（0人）
- 三国杀（0人）
- 迷你世界（0人）
- 王者荣耀（0人）
- 三角洲行动（0人）
```

实际返回内容会根据你的服务器频道、在线成员和在线时长变化。

## 通知功能说明

如果你想接收 TS3 成员上线、离线提醒，需要同时满足下面两步：

1. 在插件配置里开启 `enable_monitor`
2. 在你希望接收通知的会话里执行 `/tsnotify on` 或 `/tsbind`

只有开启监听且完成绑定的会话，才会收到通知。

如果你想自定义通知内容，可以在插件配置中修改：

- `online_message_template`
- `offline_message_template`

例如你可以把进服提示改成：

```text
{nickname} 上线了
时间：{time}
当前在线：{online_count}
```

也可以把退服提示改成：

```text
{nickname} 下线了
在线时长：{duration}
下线时间：{end_time}
```

## 常见问题

### 发送命令没有响应

请优先检查以下内容：

- 插件是否已经在 AstrBot 中成功加载
- 插件配置是否填写完整
- 机器人当前平台是否支持接收该命令
- 是否开启了纯文本触发
- 当前群是否在群白名单中

如果没有开启 `enable_plain_text_trigger`，请使用带 `/` 的命令，例如 `/ts`。

### 提示查询失败

常见原因包括：

- TS3 服务器地址填写错误
- `serverquery_port` 无法连接
- ServerQuery 账号或密码错误
- 服务器防火墙未放行对应端口

### 开启了监听但没有收到通知

请检查：

- 是否已开启 `enable_monitor`
- 当前会话是否执行过 `/tsnotify on` 或 `/tsbind`
- AstrBot 是否有权限向当前会话发送消息

### 查询很频繁会不会有影响

会有一定影响。

如果把轮询间隔设置得太低，可能增加 TS3 ServerQuery 压力，严重时还有可能触发服务器限制。建议保持默认值或根据自己服务器情况谨慎调整。

## 注意事项

- 机器人运行环境必须能够访问 TS3 的 ServerQuery 端口
- 建议为插件单独创建一个权限合适的 ServerQuery 账号
- 请妥善保管 `serverquery_password`
- 如果服务器网络有限制，请确认防火墙和安全组已放行相关端口

## 适用场景

这个插件适合希望在 AstrBot 中快速接入 TS3 状态查询和基础在线通知的用户。

如果你只想简单查一下谁在线，可以只配置基础查询功能。

如果你还想让群聊持续接收上线、离线提醒，可以再开启监听和通知绑定。
