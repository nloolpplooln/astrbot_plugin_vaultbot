# GTA 在线助手 — AstrBot 插件

基于 [astrbot_plugin_gta_online_helper](https://github.com/moemoli/astrbot_plugin_gta_online_helper) 改造，增加自建数据源（等级/金钱/240项统计），**空桑自动回退**。

## 安装

1. 将 `plugin/` 整个目录复制到 AstrBot 的 `addons/plugins/gta_online_helper/`
2. 确保 **SocialClub Query API** 已启动（`start.bat`，默认 http://localhost:8686）
3. 重启 AstrBot

## 命令

| 命令 | 数据源 | 说明 |
|---|---|---|
| `/gta 绑定 <昵称>` | — | 绑定你的 GTA 玩家名 |
| `/gta me` | 自建→空桑 + 战眼 | 查自己的生涯+封禁 |
| `/gta 生涯 <昵称>` | 自建→空桑 | 完整生涯数据 |
| `/gta 战眼 <RID/昵称>` | BattlEye UDP | 查封禁状态 |
| `/gta 更新ck <Cookie>` | — | 【管理员】更新凭证 |
| `查生涯 <昵称>` | 自建→空桑 | 快捷生涯查询 |
| `查战眼 <RID/昵称>` | BattlEye UDP | 快捷封禁查询 |

## 数据对比

| | 空桑 HQSHI | 自建 API |
|---|---|---|
| 等级 | ❌ | ✅ |
| 现金/银行 | ❌ | ✅ |
| 统计项 | ~30 项文本 | 240 项分类 |
| 稳定性 | 依赖第三方 | 完全自控 |

## 架构

```
QQ消息 → AstrBot → plugin/main.py
                    ├── socialclub_api.py  → 自建 API (localhost:8686) ← 优先
                    ├── gtaonline_helper.py → 空桑 HQSHI               ← 回退
                    └── batteye_helper.py  → BattlEye UDP
```

## 配置

在 AstrBot 插件管理面板可配置：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `plugin_log_enabled` | false | 启用详细日志 |
| `battleye_server_host` | `51.89.97.102` | 战眼服务器 |
| `battleye_server_port` | `61455` | 战眼端口 |
| `battleye_timeout_seconds` | `5` | 战眼超时 |

## 获取 Cookie

1. 浏览器登录 https://socialclub.rockstargames.com
2. F12 → Application → Cookies → 全选复制
3. 私聊机器人：`/gta 更新ck <粘贴的Cookie>`

## 安全提示

- Cookie 是敏感凭据，**只在私聊中使用 `/gta 更新ck`**
- 不要在群聊中发送 Cookie
- 如怀疑泄露，退出 R星 账号重新登录
