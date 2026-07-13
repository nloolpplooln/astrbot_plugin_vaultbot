# VaultBot — GTA 在线玩家查询

AstrBot 插件。自建 API 查询 GTA 线上玩家数据：等级/金钱/240项生涯统计/奖章/PK对比/30项异常作弊检测。

## 功能

| 命令 | 功能 |
|---|---|
| `帮助` | 显示全部指令 |
| `查生涯 <昵称>` | 完整生涯数据 + 收入/击杀/奖章异常检测 |
| `查奖章 <奖章名> [昵称]` | 奖章定义+图片+玩家进度 |
| `查战眼 <RID/昵称>` | 查封禁状态 |
| `pk <玩家1> <玩家2>` | 双方数据对比 |
| `查统计/战斗/犯罪/载具/收支/技能/武器 <昵称>` | 单项分类统计 |
| `/gta 绑定 <昵称>` | 绑定你的 GTA ID |
| `/gta me` | 查看已绑定玩家 |
| `/gta 更新ck <Cookie>` | 更新 R* Cookie（仅管理员） |

## 数据源

自建 API（优先，240项深度数据）→ 空桑 HQSHI（回退）

## 安装

1. AstrBot 面板 → 插件 → 安装 → 填入仓库地址：
   ```
   https://github.com/nloolpplooln/astrbot_plugin_vaultbot
   ```
2. 确保 [SocialClub Query API](https://github.com/nloolpplooln/socialclub-server) 已部署
3. 在插件配置中设置 `api_base_url` 为 API 服务器地址

## 配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `api_base_url` | `http://localhost:8686` | 自建 API 地址 |
| `plugin_log_enabled` | false | 启用插件日志 |
| `battleye_server_host` | `51.89.97.102` | 战眼服务器 |
| `battleye_server_port` | `61455` | 战眼端口 |
| `battleye_timeout_seconds` | `5` | 战眼超时 |

## 获取 Cookie

1. 浏览器登录 https://socialclub.rockstargames.com 并 F5 刷新
2. F12 → Application → Cookies → 全选复制
3. 私聊机器人：`/gta 更新ck <粘贴的Cookie>`
4. 或在 API 面板 http://IP:8686/setup 粘贴

## 安全

- Cookie 是敏感凭据，只在私聊使用 `/gta 更新ck`
- 不要在群聊发送 Cookie