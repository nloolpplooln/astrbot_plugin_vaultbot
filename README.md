# VaultBot — GTA Online 玩家数据查询

AstrBot 插件。直接通过 R* SCAPI 查询 GTA 线上玩家数据，无需额外 API 服务。

**核心能力**：内嵌 BearerToken 自动续期（curl_cffi Chrome 指纹），一次注入 Cookie 即可持续运行数小时甚至数天。

## 功能

### 玩家查询

| 命令 | 说明 |
|---|---|
| `查生涯 <昵称>` | 完整生涯数据（240+项统计 + 异常检测） |
| `查统计 <分类> [昵称]` | 单项分类：综合/战斗/犯罪/载具/收支/技能/武器 |
| `pk <玩家1> <玩家2>` | 双方数据对比 |
| `查奖章 <奖章名> [昵称]` | 奖章定义 + 玩家进度 |
| `查战眼 <RID/昵称>` | BattlEye 封禁状态 |

### 载具百科

| 命令 | 说明 |
|---|---|
| `查车 <关键词>` | 搜索载具（支持名称/品牌/型号/拼音） |
| `查车详情 <车名>` | 载具完整信息 + 缩略图 |
| `查品牌 <品牌>` | 列出品牌所有载具 |
| `查颜色 <关键词>` | 搜索颜色（名称/分类/HEX/ID） |

### 账号

| 命令 | 说明 |
|---|---|
| `/gta 绑定 <昵称>` | 绑定你的 GTA ID |
| `/gta me` | 查看已绑定玩家信息 |
| `/gta 更新ck <Cookie>` | 更新 R* Cookie（仅管理员私聊） |

## 安装

### 方式一：GitHub 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/nloolpplooln/astrbot_plugin_vaultbot.git
pip install curl_cffi httpx beautifulsoup4
```

### 方式一：AstrBot 面板安装

插件市场 → 填入：
```
https://github.com/nloolpplooln/astrbot_plugin_vaultbot
```

## 获取 Cookie

1. 浏览器打开并登录：https://socialclub.rockstargames.com/jobs?dateRangeCreated=any&filter=me&sort=likes&title=gtav
2. 刷新页面（F5），确保 BearerToken 是最新的
3. F12 → Application → Cookies → 全选 → 复制
4. QQ **私聊**机器人：`/gta 更新ck <粘贴的Cookie>`

Cookie 注入后插件会自动续期，无需反复操作。Token 寿命约 5 分钟，但后台每 ~4 分钟自动刷新一次。

## Token 续期机制

```
BearerToken (JWT, TTL=300s)
        ↓
    每 ~4 分钟通过 refreshaccess 续期
        ↓
    同时访问 jobs 页面续 TS* session cookies
        ↓
    429 限速 → 暂停 15 分钟自动恢复
    网络异常 → 2s/5s 退避重试
    Token 死亡 → 尝试浏览器快速恢复
```

使用 `curl_cffi` 模拟 Chrome 120 TLS 指纹，绕过 R* 的 TLS 检测。

## 环境变量（可选）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REFRESH_TTL_THRESHOLD` | `60` | TTL 低于此值（秒）触发刷新 |
| `REFRESH_JITTER_SECONDS` | `5` | 随机抖动范围 |
| `THROTTLE_PAUSE_MINUTES` | `15` | 429 限速后暂停时长 |
| `RSC_EMAIL` | - | R* 邮箱（可选，浏览器自动恢复用） |
| `RSC_PASSWORD` | - | R* 密码（可选，浏览器自动恢复用） |

## 数据源

直接调用 R* 内部 SCAPI（`scapi.rockstargames.com`），无第三方中转。

查询自己或其他玩家均需有效的 R* 登录 Cookie（BearerToken + TS* session cookies）。

## 注意事项

- Cookie 是敏感凭据，只在**私聊**使用 `/gta 更新ck`
- 不要在群聊发送 Cookie（会在群聊记录中暴露）
- 如果 Token 续期失败超过 3 次，系统会尝试浏览器自动恢复（需要 Playwright + Chromium）
- 浏览器自动恢复需要 Docker 镜像包含 Playwright，或手动安装：`pip install playwright && python -m playwright install chromium`

## 致谢

基于 [astrbot_plugin_gta_online_helper](https://github.com/moemoli/astrbot_plugin_gta_online_helper) 重构而来。
