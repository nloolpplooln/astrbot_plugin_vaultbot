import os
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.core.message.components import Plain, Image
from .batteye_helper import (
    BATTLEYE_SERVER_HOST,
    BATTLEYE_SERVER_PORT,
    BATTLEYE_TIMEOUT_SECONDS,
    check_battleye_by_name,
    check_battleye_by_rid,
    configure_battleye,
)
from .gtaonline_helper import (
    get_hqshi_recent_text,
    get_hqshi_status,
    is_plugin_log_enabled,
    parse_cookie_string,
    set_plugin_log_enabled,
    set_authorization,
    set_refresh_persist_callback,
    set_refresh_cookies,
    update_from_cookie_string,
)
from .socialclub_api import (
    CATEGORY_ALIASES,
    check_health,
    format_awards_text,
    format_career_text,
    format_category_text,
    format_compare_text,
    format_local_awards_result,
    format_profile_text,
    query_awards,
    query_player,
    search_local_awards,
    set_api_base_url,
    ApiError as SocialClubApiError,
)

AUTHORIZATION_KV_KEY = "authorization"
REFRESH_COOKIES_KV_KEY = "refresh_cookies"
USER_BINDINGS_KV_KEY = "user_bindings"
REQUIRED_COOKIE_FIELDS = (
    "BearerToken",
    "TS01008f56",
    "TS011be943",
    "TS01347d69",
    "RockStarWebSessionId",
    "prod",
)

class GTAOnlinePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self.config = config

    def _apply_battleye_config(self) -> None:
        cfg = self.config if isinstance(self.config, dict) else {}

        host = str(cfg.get("battleye_server_host") or BATTLEYE_SERVER_HOST).strip()

        raw_port = cfg.get("battleye_server_port", BATTLEYE_SERVER_PORT)
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            port = BATTLEYE_SERVER_PORT

        raw_timeout = cfg.get("battleye_timeout_seconds", BATTLEYE_TIMEOUT_SECONDS)
        try:
            timeout_seconds = int(raw_timeout)
        except (TypeError, ValueError):
            timeout_seconds = BATTLEYE_TIMEOUT_SECONDS

        if port <= 0:
            port = BATTLEYE_SERVER_PORT
        if timeout_seconds <= 0:
            timeout_seconds = BATTLEYE_TIMEOUT_SECONDS

        configure_battleye(host=host, port=port, timeout_seconds=timeout_seconds)
        if is_plugin_log_enabled():
            logger.info(
                "[gta_online_helper] BattlEye config applied: host=%s, port=%s, timeout=%ss",
                host,
                port,
                timeout_seconds,
            )

    def _apply_log_config(self) -> None:
        cfg = self.config if isinstance(self.config, dict) else {}
        enabled = bool(cfg.get("plugin_log_enabled", True))
        set_plugin_log_enabled(enabled)

        if is_plugin_log_enabled():
            logger.info("[gta_online_helper] Plugin informational logs enabled.")

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        self._apply_log_config()
        self._apply_battleye_config()
        # 读取 API 地址配置
        cfg = self.config if isinstance(self.config, dict) else {}
        api_url = str(cfg.get("api_base_url", "http://localhost:8686")).strip()
        if api_url:
            set_api_base_url(api_url)
            if is_plugin_log_enabled():
                logger.info("[gta_online_helper] 自建 API 地址: %s", api_url)
        set_refresh_persist_callback(self._persist_auth_state)

        authorization = await self.get_kv_data(AUTHORIZATION_KV_KEY, "")
        if isinstance(authorization, str) and authorization.strip():
            if is_plugin_log_enabled():
                logger.info("[gta_online_helper] Found saved Authorization in plugin storage, loading it: %s", authorization)
            set_authorization(authorization)
            if is_plugin_log_enabled():
                logger.info("[gta_online_helper] Authorization loaded from plugin storage.")

        refresh_cookies = await self.get_kv_data(REFRESH_COOKIES_KV_KEY, {})
        if isinstance(refresh_cookies, dict) and refresh_cookies:
            safe_cookies = {
                str(k): str(v)
                for k, v in refresh_cookies.items()
                if isinstance(k, str) and v is not None and str(v).strip()
            }
            if safe_cookies:
                set_refresh_cookies(safe_cookies)
                if is_plugin_log_enabled():
                    logger.info("[gta_online_helper] Refresh cookies loaded from plugin storage.")

    async def _persist_auth_state(self, authorization: str, refresh_cookies: dict[str, str]) -> None:
        """Persist refreshed authorization and cookies immediately."""
        if authorization and authorization.strip():
            await self.put_kv_data(AUTHORIZATION_KV_KEY, authorization)
        if refresh_cookies:
            await self.put_kv_data(REFRESH_COOKIES_KV_KEY, refresh_cookies)

    async def _load_user_bindings(self) -> dict[str, str]:
        data = await self.get_kv_data(USER_BINDINGS_KV_KEY, {})
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in data.items():
            user_id = str(k).strip()
            nickname = str(v).strip()
            if user_id and nickname:
                out[user_id] = nickname
        return out

    async def _save_user_bindings(self, bindings: dict[str, str]) -> None:
        await self.put_kv_data(USER_BINDINGS_KV_KEY, bindings)

    def _extract_sender_id(self, event: AstrMessageEvent) -> str:
        sender_id = str(event.get_sender_id() or "").strip()
        return sender_id

    @staticmethod
    def _sanitize_message_tail(text: str) -> str:
        return re.sub(
            r"\s*\[(?:MSG[_ ]?ID)\s*:\s*\d+\]\s*$",
            "",
            str(text or "").strip(),
            flags=re.IGNORECASE,
        ).strip()

    async def _get_bound_nickname(self, event: AstrMessageEvent) -> str:
        sender_id = self._extract_sender_id(event)
        if not sender_id:
            return ""
        bindings = await self._load_user_bindings()
        return bindings.get(sender_id, "").strip()

    def _parse_group_third_arg(self, event: AstrMessageEvent) -> str:
        message_str = self._sanitize_message_tail(event.message_str)
        parts = message_str.split(maxsplit=2)
        if len(parts) >= 3:
            return parts[2].strip()
        return ""

    @filter.command_group("gta")
    def gta(self):
        pass

    @gta.command("绑定", alias={"bind"})
    async def gta_bind(self, event: AstrMessageEvent, nickname: str | None = None):
        """绑定 GTA 玩家名称。/gta 绑定 <玩家名称>"""
        sender_id = self._extract_sender_id(event)
        if not sender_id:
            yield event.plain_result("无法识别你的用户标识，暂时不能绑定。")
            return

        target = str(nickname or "").strip()
        if not target:
            target = self._parse_group_third_arg(event)

        if not target:
            yield event.plain_result("用法: /gta 绑定 <玩家名称>")
            return

        bindings = await self._load_user_bindings()
        bindings[sender_id] = target
        await self._save_user_bindings(bindings)
        yield event.plain_result(f"绑定成功，你的 GTA 玩家名称已设置为: {target}")

    @gta.command("我", alias={"me", "my"})
    async def gta_me(self, event: AstrMessageEvent):
        """查询自己已绑定的生涯与战眼信息。/gta me"""
        sender_id = self._extract_sender_id(event)
        if not sender_id:
            yield event.plain_result("无法识别你的用户标识。")
            return

        nickname = await self._get_bound_nickname(event)
        if not nickname:
            yield event.plain_result("你还没有绑定玩家名称。请先使用: /gta 绑定 <玩家名称>")
            return

        lines = [f"已绑定玩家: {nickname}"]

        # 优先用自建 API（240项深度数据），失败回退到空桑
        try:
            body = await query_player(nickname)
            lines.append("\n生涯信息(自建)")
            lines.append(format_career_text(body))
        except SocialClubApiError as e:
            logger.warning("[gta_online_helper] 自建API查询失败，回退空桑: %s", e)
            try:
                career_text = await get_hqshi_recent_text(nickname)
                lines.append("\n生涯信息(空桑)")
                lines.append(career_text)
            except Exception as e2:
                lines.append("\n生涯信息")
                lines.append(f"查询失败: {e2}")

        try:
            be_result = await check_battleye_by_name(nickname)
            lines.append("\n战眼信息")
            lines.append(f"RID: {be_result.get('rid', '-')}")
            if be_result.get("is_banned"):
                lines.append("状态: 已封禁")
                lines.append(f"原因: {be_result.get('ban_reason') or '-'}")
            else:
                lines.append("状态: 未封禁")
        except Exception as e:
            lines.append("\n战眼信息")
            lines.append(f"查询失败: {e}")

        yield event.plain_result("\n".join(lines))

    @gta.command("生涯", alias={"career"})
    async def gta_career(self, event: AstrMessageEvent, nickname: str | None = None):
        """查询生涯信息。/gta 生涯 [强制/-f] [玩家昵称]"""
        message_str = str(event.message_str or "").strip()
        force = any(f in message_str for f in [" -f", " -F", "强制", "刷新", " force"])

        target = str(nickname or "").strip()
        for flag in ["-f ", "-F ", "force "]:
            if target.startswith(flag):
                target = target[len(flag):].strip()
                force = True
                break
        if target.lower() in ("-f", "--force", "强制", "刷新"):
            target = ""

        if not target:
            target = self._parse_group_third_arg(event)
            for flag in ["-f ", "-F ", "force "]:
                if target.startswith(flag):
                    target = target[len(flag):].strip()
                    force = True
                    break
        if not target:
            target = await self._get_bound_nickname(event)
        if not target:
            yield event.plain_result("用法: /gta 生涯 [强制/-f] <玩家昵称>\n-f 或 强制 = 强制刷新跳过缓存")
            return

        # 优先用自建 API，失败回退空桑
        try:
            body = await query_player(target, force=force)
            yield event.plain_result(format_career_text(body))
            return
        except SocialClubApiError as e:
            logger.warning("[gta_online_helper] 自建API查询失败，回退空桑: %s", e)

        recent_error = None
        try:
            text = await get_hqshi_recent_text(target)
            yield event.plain_result(text)
            return
        except Exception as e:
            recent_error = e
            logger.warning("[gta_online_helper] HQSHI recent query failed: %s", e)

        try:
            status = await get_hqshi_status(target, limit=3)
        except Exception as status_error:
            yield event.plain_result(
                f"生涯查询失败: {status_error}\nrecent详情: {recent_error or '-'}"
            )
            return

        lines = [
            "生涯查询结果(HQSHI)",
            f"昵称: {status.get('名称') or status.get('昵称') or target}",
            f"RID: {status.get('rockstar_id') or '-'}",
            f"最近游玩: {status.get('最近游玩') or '-'}",
            f"状态更新: {status.get('状态更新') or '-'}",
            f"所在地: {status.get('所在地') or '-'}",
        ]
        yield event.plain_result("\n".join(lines))

    @gta.command("战眼", alias={"be", "battleye"})
    async def gta_battleye(self, event: AstrMessageEvent, identifier: str | None = None):
        """查询战眼封禁。/gta 战眼 [RID或玩家名称]"""
        target = str(identifier or "").strip()
        if not target:
            target = self._parse_group_third_arg(event)
        if not target:
            target = await self._get_bound_nickname(event)
        if not target:
            yield event.plain_result("用法: /gta 战眼 <RID或玩家名称>，或先 /gta 绑定 <玩家名称>")
            return

        try:
            if target.isdigit():
                result = await check_battleye_by_rid(int(target))
            else:
                result = await check_battleye_by_name(target)
        except Exception as e:
            yield event.plain_result(f"战眼查询失败: {e}")
            return

        lines = [
            "战眼查询结果",
            f"RID: {result.get('rid', '-')}",
        ]
        if result.get("name"):
            lines.append(f"玩家: {result['name']}")

        if result.get("is_banned"):
            lines.append("状态: 已封禁")
            lines.append(f"原因: {result.get('ban_reason', '') or '-'}")
        else:
            lines.append("状态: 未封禁")

        yield event.plain_result("\n".join(lines))

    @gta.command("帮助", alias={"help", "h"})
    async def gta_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "用法:\n"
            "/gta 绑定 <玩家名称>\n"
            "/gta me — 查询已绑定玩家（等级+战眼）\n"
            "/gta 生涯 [玩家昵称] — 完整生涯数据（自建240项）\n"
            "/gta 战眼 [RID或玩家名称]\n"
            "/gta 更新ck <Cookie字符串>\n"
            "查生涯 <昵称> — 快捷查询\n"
            "查战眼 <RID或昵称> — 快捷查封禁\n"
            "\n数据源: 自建API(优先) + 空桑(回退)"
        )

    @gta.command("更新ck", alias={"更新CK", "setck", "ck"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gta_set_auth(self, event: AstrMessageEvent):
        """更新 Authorization 或 Cookie。/gta 更新ck <BearerToken 或 Cookie字符串>"""
        message_str = event.message_str.strip()

        payload = ""
        parts = message_str.split(maxsplit=1)
        if len(parts) >= 2:
            payload = parts[1].strip()

        # Strip adapter-appended message markers, e.g. [MSG_ID:1211303900] / [MSGID:1211303900].
        payload = re.sub(r"\s*\[(?:MSG[_ ]?ID)\s*:\s*\d+\]\s*$", "", payload, flags=re.IGNORECASE)
        payload = payload.strip()

        if not payload or not payload.strip():
            yield event.plain_result("用法: /gta 更新ck <BearerToken 或完整Cookie字符串>")
            return

        payload = payload.strip()
        if "=" in payload:
            parsed = parse_cookie_string(payload)
            if not parsed:
                yield event.plain_result("CK 解析失败，请检查格式，例如: key=1;key2=2")
                return

            missing_fields = [field for field in REQUIRED_COOKIE_FIELDS if not parsed.get(field)]
            if missing_fields:
                yield event.plain_result(
                    f"CK 缺少必需字段: {', '.join(missing_fields)}"
                )
                return

            parsed = update_from_cookie_string(payload)

            # Persist all cookie key-values for future refresh requests.
            await self.put_kv_data(REFRESH_COOKIES_KV_KEY, parsed)

            token = parsed.get("BearerToken", "").strip()
            if token:
                set_authorization(token)
                await self.put_kv_data(AUTHORIZATION_KV_KEY, token)
                masked = f"{token[:8]}..." if len(token) > 8 else "***"
                yield event.plain_result(
                    f"Cookie 已更新并缓存，Authorization 已更新: {masked}"
                )
            else:
                yield event.plain_result("Cookie 已缓存（未检测到 BearerToken）。")
            return

        authorization = payload
        set_authorization(authorization)
        await self.put_kv_data(AUTHORIZATION_KV_KEY, authorization)

        # Avoid echoing sensitive tokens in full.
        masked = f"{authorization[:8]}..." if len(authorization) > 8 else "***"
        yield event.plain_result(f"Authorization 已更新并持久化: {masked}")

    @filter.command("查战眼")
    async def gta_battleye_check(self, event: AstrMessageEvent, identifier: str = ""):
        """查询玩家战眼封禁。查战眼 <RID或玩家名称>"""
        if not identifier or not identifier.strip():
            yield event.plain_result("用法: /查战眼 <RID或玩家名称>")
            return

        identifier = identifier.strip()
        try:
            if identifier.isdigit():
                result = await check_battleye_by_rid(int(identifier))
            else:
                result = await check_battleye_by_name(identifier)
        except Exception as e:
            yield event.plain_result(f"战眼查询失败: {e}")
            return

        lines = [
            "战眼查询结果",
            f"RID: {result.get('rid', '-')}",
        ]
        if result.get("name"):
            lines.append(f"玩家: {result['name']}")

        if result.get("is_banned"):
            lines.append("状态: 已封禁")
            lines.append(f"原因: {result.get('ban_reason', '') or '-'}")
        else:
            lines.append("状态: 未封禁")

        yield event.plain_result("\n".join(lines))

    @filter.command("查生涯")
    async def gta_career_query(self, event: AstrMessageEvent, nickname: str = ""):
        """查询生涯数据。查生涯 [强制/-f] <玩家昵称>"""
        message_str = str(event.message_str or "").strip()
        # 检测强制刷新标记：-f、强制、刷新、force
        force = any(f in message_str for f in [" -f", " -F", "强制", "刷新", " force"])

        target = nickname.strip()
        # 解析 -f/强制 标记
        for flag in ["-f ", "-F ", "force "]:
            if target.startswith(flag):
                target = target[len(flag):].strip()
                force = True
                break
        # target 本身是 flag 关键词（比如只发了 -f 或 强制）
        if target.lower() in ("-f", "--force", "强制", "刷新", "force"):
            force = True
            target = ""
        # flag 在 nickname 参数里但后面没跟昵称，从 message 里取
        if not target:
            # message_str 格式: "查生涯 -f oolpploo" 或 "查生涯 强制 oolpploo"
            parts = message_str.split()
            # 跳过命令本身 + flag
            for i, p in enumerate(parts):
                if i > 0 and p.lower() in ("-f", "--force", "强制", "刷新", "force"):
                    target = " ".join(parts[i+1:]) if i+1 < len(parts) else ""
                    break
            # 如果没有 flag 标记，取第一个参数作为昵称
            if not target and len(parts) > 1:
                target = parts[1] if parts[1].lower() not in ("-f", "--force", "强制", "刷新", "force") else ""

        if not target:
            yield event.plain_result("用法: 查生涯 [-f/强制] <玩家昵称>\n-f 或 强制 = 强制刷新跳过缓存")
            return

        # 优先自建 API
        try:
            body = await query_player(target, force=force)
            yield event.plain_result(format_career_text(body))
            return
        except SocialClubApiError as e:
            logger.warning("[gta_online_helper] 自建API查询失败，回退空桑: %s", e)

        recent_error = None
        try:
            text = await get_hqshi_recent_text(target)
            yield event.plain_result(text)
            return
        except Exception as e:
            recent_error = e
            logger.warning("[gta_online_helper] HQSHI recent query failed: %s", e)

        # Fallback to status data if recent text is unavailable.
        try:
            status = await get_hqshi_status(target, limit=3)
        except Exception as status_error:
            yield event.plain_result(
                f"生涯查询失败: {status_error}\nrecent详情: {recent_error or '-'}"
            )
            return

        lines = [
            "生涯查询结果(HQSHI)",
            f"昵称: {status.get('名称') or status.get('昵称') or target}",
            f"RID: {status.get('rockstar_id') or '-'}",
            f"最近游玩: {status.get('最近游玩') or '-'}",
            f"状态更新: {status.get('状态更新') or '-'}",
            f"所在地: {status.get('所在地') or '-'}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("查统计")
    async def gta_category_query(self, event: AstrMessageEvent, category: str = "", nickname: str = ""):
        """查询指定分类。查统计 <分类> [昵称]  分类: 战斗/犯罪/载具/收支/技能/武器/生涯/综合"""
        cat = category.strip().lower()
        if not cat:
            yield event.plain_result("用法: 查统计 <分类> [昵称]\n分类: 生涯 综合 战斗 犯罪 载具 收支 技能 武器")
            return

        # 解析中文别名 → 英文 key
        cat_key = cat
        for en, zh in CATEGORY_ALIASES.items():
            if cat == zh or cat == en:
                cat_key = en
                break

        if cat_key not in CATEGORY_ALIASES:
            yield event.plain_result(f"未知分类: {cat}\n可选: {', '.join(CATEGORY_ALIASES.values())}")
            return

        target = nickname.strip()
        if not target:
            # Try parsing from message: "查统计 战斗 oolpploo"
            parts = str(event.message_str or "").strip().split()
            if len(parts) >= 3:
                target = parts[2]
        if not target:
            target = await self._get_bound_nickname(event)
        if not target:
            yield event.plain_result(f"用法: 查统计 {CATEGORY_ALIASES[cat_key]} <玩家昵称>")
            return

        try:
            body = await query_player(target)
            text = format_category_text(body, cat_key)
            yield event.plain_result(text)
        except SocialClubApiError:
            yield event.plain_result(f"查询失败，API 服务离线或 token 过期。")

    @filter.command("查战斗")
    async def gta_combat(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查战斗 <昵称>")
            return
        try:
            body = await query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "combat"))
        except SocialClubApiError:
            yield event.plain_result("查询失败")

    @filter.command("查犯罪")
    async def gta_crimes(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查犯罪 <昵称>")
            return
        try:
            body = await query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "crimes"))
        except SocialClubApiError:
            yield event.plain_result("查询失败")

    @filter.command("查载具")
    async def gta_vehicles(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查载具 <昵称>")
            return
        try:
            body = await query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "vehicles"))
        except SocialClubApiError:
            yield event.plain_result("查询失败")

    @filter.command("查收支")
    async def gta_cash(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查收支 <昵称>")
            return
        try:
            body = await query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "cash"))
        except SocialClubApiError:
            yield event.plain_result("查询失败")

    @filter.command("查技能")
    async def gta_skills(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查技能 <昵称>")
            return
        try:
            body = await query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "skills"))
        except SocialClubApiError:
            yield event.plain_result("查询失败")

    @filter.command("查武器")
    async def gta_weapons(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查武器 <昵称>")
            return
        try:
            body = await query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "weapons"))
        except SocialClubApiError:
            yield event.plain_result("查询失败")

    @filter.command("pk")
    async def gta_compare(self, event: AstrMessageEvent, nickname: str = ""):
        """PK对比。pk <玩家1> <玩家2> 或 pk <对手> (自动用绑定ID)"""
        parts = str(event.message_str or "").strip().split()
        names = [p for p in parts if p not in ("pk", "/pk")]
        if len(names) >= 2:
            n1, n2 = names[0], names[1]
        elif len(names) == 1:
            # 只有一个参数 → 自动用绑定昵称
            bound = await self._get_bound_nickname(event)
            if not bound:
                yield event.plain_result("你还没有绑定ID，用法: pk <玩家1> <玩家2>\n或先 /gta 绑定 <你的昵称>")
                return
            n1, n2 = bound, names[0]
        else:
            yield event.plain_result("用法: pk <玩家1> <玩家2>\n或 pk <对手> (自动用你绑定的ID)")
            return
        try:
            b1, b2 = await query_player(n1), await query_player(n2)
            yield event.plain_result(format_compare_text(b1, n1, b2, n2))
        except SocialClubApiError as e:
            yield event.plain_result(f"查询失败: {e}")

    @filter.command("查奖章")
    async def gta_awards(self, event: AstrMessageEvent, nickname: str = ""):
        """查奖章 <关键词> [昵称] → 奖章定义+玩家进度（昵称可选，缺省用绑定ID）"""
        parts = str(event.message_str or "").strip().split()
        args = parts[1:] if len(parts) > 1 else []

        if not args:
            yield event.plain_result("用法: 查奖章 <奖章名> [昵称]\n如: 查奖章 罪神  |  查奖章 一杆进洞 oolpploo")
            return

        # 解析：最后一个参数如果是纯英文数字可能是昵称，否则全部当搜索词
        keyword = ""
        target = ""
        if len(args) >= 2:
            last = args[-1]
            # 最后一个看起来像玩家名 → 作为昵称
            if re.match(r'^[a-zA-Z0-9_\-\.]+$', last):
                keyword = " ".join(args[:-1])
                target = last
            else:
                keyword = " ".join(args)
        else:
            keyword = args[0]

        # 搜索本地奖章库
        local_matches = search_local_awards(keyword)

        # 没找到本地匹配 → 可能 keyword 是昵称，查全览
        if not local_matches and len(args) == 1:
            try:
                body = await query_awards(keyword)
                yield event.plain_result(format_awards_text(body))
                return
            except SocialClubApiError as e:
                yield event.plain_result(f"未找到「{keyword}」相关奖章，查玩家也失败: {e}")
                return

        if not local_matches:
            yield event.plain_result(f"未找到包含「{keyword}」的奖章。")
            return

        # 确定要查的玩家：指定了就用指定的，否则用绑定ID
        if not target:
            target = await self._get_bound_nickname(event)

        # 查玩家进度
        player_progress = {}
        if target:
            try:
                body = await query_awards(target)
                items = body.get("awards", {}).get("_items", [])
                for n, m, d, t in items:
                    player_progress[n.lower()] = (m, d, t)
            except SocialClubApiError:
                pass  # 查不到就算了

        # 输出
        text = format_local_awards_result(local_matches, keyword, target, player_progress)
        chain = [Plain(text)]
        plugin_dir = os.path.dirname(__file__)
        for a in local_matches:
            img_path = a.get("image", "")
            if img_path:
                abs_path = os.path.join(plugin_dir, img_path)
                if os.path.exists(abs_path):
                    chain.append(Image.fromFileSystem(abs_path))
        yield event.chain_result(chain)

    @filter.command("帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "==== GTA Online 助手 ====\n"
            "=== 绑定 ===\n"
            "/gta 绑定 <玩家名> — 绑定你的GTA ID\n"
            "/gta me — 查看绑定玩家信息\n"
            "\n=== 查询 ===\n"
            "查生涯 <昵称> — 完整生涯数据(+异常检测)\n"
            "查奖章 <奖章名> [昵称] — 奖章定义+进度(缺省昵称用绑定ID)\n"
            "查战眼 <RID/昵称> — 查封禁状态\n"
            "pk <玩家1> <玩家2> — 双方对比\n"
            "\n=== 单项统计 ===\n"
            "查统计 <昵称> / 查战斗 <昵称> / 查犯罪 <昵称>\n"
            "查载具 <昵称> / 查收支 <昵称> / 查技能 <昵称>\n"
            "查武器 <昵称>\n"
            "\n=== 管理 ===\n"
            "/gta 更新ck <cookie> — 更新凭证(仅管理员)\n"
            "\n=== 其他 ===\n"
            "帮助 — 显示此帮助"
        )

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
