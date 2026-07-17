import asyncio
import datetime
import os
import random
import re
import time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.core.message.components import Plain, Image

from .services import auth, db, query as query_svc
from .services.config import get_settings
from .services.scapi import ScapiError
from .services.webstats import WebStatsError
from .services.auth import AuthError

from .batteye_helper import (
    BATTLEYE_SERVER_HOST,
    BATTLEYE_SERVER_PORT,
    BATTLEYE_TIMEOUT_SECONDS,
    check_battleye_by_name,
    check_battleye_by_rid,
    configure_battleye,
)
from .vehicle_search import (
    get_color_by_id,
    get_color_by_name,
    get_colors_by_name,
    get_color_image,
    get_vehicle_by_name,
    get_vehicles_by_brand,
    search_colors,
    search_vehicles,
    format_brand_list,
    format_color_detail,
    format_color_list,
    format_vehicle_detail,
    format_vehicle_list,
)

# 格式化函数仍从 socialclub_api 导入（纯输出层）
from .socialclub_api import (
    CATEGORY_ALIASES,
    format_awards_text,
    format_career_text,
    format_category_text,
    format_compare_text,
    format_local_awards_result,
    format_profile_text,
    search_local_awards,
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
        logger.info(
            "[gta_online_helper] BattlEye config: host=%s, port=%s, timeout=%ss",
            host, port, timeout_seconds,
        )

    async def initialize(self):
        """初始化：加载配置、凭证、启动后台续期。"""
        self._apply_battleye_config()

        # 初始化 DB 并加载 SQLite 中的凭证
        db.init_db()
        auth.load_from_storage()

        # 从 AstrBot KV 存储恢复凭证（覆盖 SQLite，优先使用 KV）
        authorization = await self.get_kv_data(AUTHORIZATION_KV_KEY, "")
        if isinstance(authorization, str) and authorization.strip():
            auth.set_authorization(authorization)
            logger.info("[gta_online_helper] Authorization loaded from plugin storage.")
        refresh_cookies = await self.get_kv_data(REFRESH_COOKIES_KV_KEY, {})
        if isinstance(refresh_cookies, dict) and refresh_cookies:
            safe_cookies = {
                str(k): str(v)
                for k, v in refresh_cookies.items()
                if isinstance(k, str) and v is not None and str(v).strip()
            }
            if safe_cookies:
                auth.set_refresh_cookies(safe_cookies)
                logger.info("[gta_online_helper] Refresh cookies loaded from plugin storage.")

        # KV 加载完成后再启动续期循环
        has_auth = bool(auth.get_authorization())
        has_cookies = not auth.missing_refresh_keys()
        if has_auth and has_cookies:
            logger.info("[gta_online_helper] 凭证已加载，启动智能续期（阈值 %ds ±%ds，JWT exp 驱动）",
                        get_settings().refresh_ttl_threshold, get_settings().refresh_jitter_seconds)
        else:
            logger.warning("[gta_online_helper] 凭证不完整（auth=%s cookies=%s），尝试浏览器自动登录…",
                           has_auth, has_cookies)
            try:
                from .services import login as _login
                await _login.recover()
                logger.info("[gta_online_helper] 浏览器自动登录成功，凭证已恢复")
            except Exception as e:
                logger.warning("[gta_online_helper] 浏览器自动登录失败: %s，等待手动注入 Cookie", e)
        # 始终启动续期循环
        self._refresh_task = asyncio.ensure_future(self._auto_refresh_loop())

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        """判断异常是否为网络/连接问题（非认证/业务错误）。"""
        err_str = str(exc).lower()
        exc_name = type(exc).__name__.lower()
        keywords = [
            'timeout', 'timed out',
            'connection', 'connectionerror',
            'tls', 'ssl', 'tlsv1',
            'curl error', 'curl_easy',
            'remote disconnected', 'remote end',
            'reset by peer', 'connection reset',
            'name or service not known', 'getaddrinfo',
            'eof', 'broken pipe',
            'requestserror', 'requestsexception',
            'network', 'socket',
        ]
        return any(kw in err_str or kw in exc_name for kw in keywords)

    async def _try_immediate_refresh(self):
        """注入 Cookie 后立即续期，趁 token 还有效。"""
        try:
            await self._do_refresh(req_id="CK-INJECT")
            logger.info("[refresh][CK-INJECT] Cookie 注入后立即续期成功")
        except Exception as e:
            logger.warning("[refresh][CK-INJECT] Cookie 注入后立即续期失败: %s", e)

    async def _do_refresh(self, req_id: str = ""):
        """单次完整续期：refreshaccess → 续 CK → 持久化。"""
        loop = asyncio.get_running_loop()
        ttl_before = auth.token_ttl_seconds()
        prefix = f"[refresh][{req_id}] " if req_id else "[refresh] "

        refresh_start = time.monotonic()
        token = await loop.run_in_executor(None, auth.refresh_authorization, 10)
        refresh_ms = int((time.monotonic() - refresh_start) * 1000)

        # 重新解析新 JWT（auth._authorization 已被 refresh_authorization 更新）
        ttl_after = auth.token_ttl_seconds()
        exp_after = int(time.time()) + ttl_after
        s = get_settings()
        next_at = datetime.datetime.now() + datetime.timedelta(
            seconds=max(0, ttl_after - s.refresh_ttl_threshold)
        )
        logger.info(
            "%s✓ 刷新成功 | HTTP：200 | 耗时：%d ms | 新 TTL：%d 秒 | 新 exp：%d | 下次刷新：%s",
            prefix, refresh_ms, ttl_after, exp_after, next_at.strftime("%H:%M:%S"),
        )

        cookie_count = await loop.run_in_executor(None, auth.refresh_session_cookies, 15)
        if cookie_count:
            logger.info("%s收集到 %d 个会话 Cookie", prefix, cookie_count)

        # 持久化到 AstrBot KV 存储
        try:
            await self.put_kv_data(AUTHORIZATION_KV_KEY, auth.get_authorization())
            await self.put_kv_data(REFRESH_COOKIES_KV_KEY, auth.get_refresh_cookies())
        except Exception as e:
            logger.warning("%sKV 持久化失败: %s", prefix, e)

        return token

    async def _auto_refresh_loop(self):
        """智能续期调度：JWT exp 驱动睡眠 + 随机抖动 + 网络异常立即重试。

        流程：
        1. 解码 JWT exp，计算 sleep = TTL - threshold ± jitter
        2. sleep 到目标时间后刷新
        3. 网络异常 → 2s/5s 退避重试（TTL<15s 则放弃）
        4. 429 → 暂停 15 分钟（保持原逻辑）
        5. 刷新成功 → 重新计算下一次刷新时间
        """
        import threading as _thr

        s = get_settings()
        fail_count = 0
        throttled = False

        # ── 运行统计 ──
        start_time = time.monotonic()
        stats = {"total": 0, "success": 0, "consecutive": 0, "fail": 0, "http_429": 0, "timeout": 0}

        # ── 诊断状态 ──
        diag = {
            "req_seq": 0,
            "last_mono": 0.0,          # 上次 refreshaccess 开始的 monotonic
            "prev_end_mono": 0.0,       # 上次 refreshaccess 结束的 monotonic（用于间隔计算）
            "total_ms": 0,
            "ms_count": 0,
            "min_interval": 999999,
            "max_interval": 0,
            "today_date": "",
            "today_count": 0,
            "last_stats_hour": -1,
        }
        _current_req_id = ""
        _current_req_start_mono = 0.0

        def _stats_line() -> str:
            elapsed = int(time.monotonic() - start_time)
            h, m = elapsed // 3600, (elapsed % 3600) // 60
            return (
                f"[refresh] Stats | 运行：{h}h {m}m | 刷新：{stats['total']} 次 | "
                f"成功：{stats['success']} | 失败：{stats['fail']} | "
                f"429：{stats['http_429']} | timeout：{stats['timeout']}"
            )

        def _hourly_stats_check():
            """每小时输出一次累计统计。"""
            current_hour = int(time.monotonic() - start_time) // 3600
            if current_hour > diag["last_stats_hour"]:
                diag["last_stats_hour"] = current_hour
                avg_ms = diag["total_ms"] // diag["ms_count"] if diag["ms_count"] > 0 else 0
                min_int = diag["min_interval"] if diag["min_interval"] < 999999 else 0
                max_int = diag["max_interval"]
                logger.info(
                    "[refresh] Hourly Stats | %s | 平均耗时：%d ms | 最短间隔：%d 秒 | 最长间隔：%d 秒",
                    _stats_line(), avg_ms, min_int, max_int,
                )

        def _pre_refresh_diag(ttl: int) -> str:
            """刷新前诊断：生成 req_id、重复检测、输出请求信息。返回 req_id。"""
            nonlocal _current_req_id, _current_req_start_mono
            import datetime as _dt

            diag["req_seq"] += 1
            req_id = f"REQ-{diag['req_seq']:06d}"
            _current_req_id = req_id

            now_mono = time.monotonic()
            interval = int(now_mono - diag["last_mono"]) if diag["last_mono"] > 0 else -1

            # 重复刷新检测
            if 0 < interval < 30:
                logger.warning(
                    "[refresh][%s] ⚠ 检测到异常 refresh，距上次仅 %d 秒，请检查是否存在多个后台线程",
                    req_id, interval,
                )

            # 今日计数
            today_str = _dt.date.today().isoformat()
            if diag["today_date"] != today_str:
                diag["today_date"] = today_str
                diag["today_count"] = 0

            exp_now = int(time.time()) + ttl
            logger.info(
                "[refresh][%s] 开始 refreshaccess | Thread：%s | 距上次：%d 秒 | TTL：%d 秒 | exp：%d | 连续成功：%d 次 | 今日刷新：%d 次",
                req_id, _thr.current_thread().ident, interval, ttl, exp_now,
                stats["consecutive"], diag["today_count"],
            )

            diag["last_mono"] = now_mono
            _current_req_start_mono = now_mono
            return req_id

        def _post_refresh_success():
            """刷新成功后更新诊断数据。"""
            now_mono = time.monotonic()
            elapsed_ms = int((now_mono - _current_req_start_mono) * 1000) if _current_req_start_mono > 0 else 0
            diag["total_ms"] += elapsed_ms
            diag["ms_count"] += 1
            diag["today_count"] += 1

            if diag["prev_end_mono"] > 0:
                gap = int(_current_req_start_mono - diag["prev_end_mono"])
                if gap < diag["min_interval"]:
                    diag["min_interval"] = gap
                if gap > diag["max_interval"]:
                    diag["max_interval"] = gap
            diag["prev_end_mono"] = now_mono

        def _post_refresh_fail(http_info: str):
            """刷新失败后输出诊断日志。"""
            now_mono = time.monotonic()
            elapsed_ms = int((now_mono - _current_req_start_mono) * 1000) if _current_req_start_mono > 0 else 0
            diag["prev_end_mono"] = now_mono
            interval = int(_current_req_start_mono - diag["last_mono"]) if diag["last_mono"] > 0 else -1
            logger.warning(
                "[refresh][%s] ✗ 失败 | HTTP：%s | 耗时：%d ms | 距上次：%d 秒",
                _current_req_id, http_info, elapsed_ms,
                interval if interval >= 0 else -1,
            )

        while True:
            try:
                ttl = auth.token_ttl_seconds()

                if ttl <= 0:
                    # Token 已过期，尝试抢救性刷新
                    logger.info("[refresh] Token 已过期，尝试刷新…")
                    req_id = _pre_refresh_diag(ttl)
                    await self._do_refresh(req_id=req_id)
                    _post_refresh_success()
                    stats["total"] += 1
                    stats["success"] += 1
                    stats["consecutive"] += 1
                    logger.info("[refresh][%s] 连续成功：%d 次", req_id, stats["consecutive"])
                    fail_count = 0
                    _hourly_stats_check()
                    continue  # 刷新成功后重新调度

                if ttl < s.refresh_ttl_threshold and not throttled:
                    # 已进入刷新窗口，立即刷新
                    jitter = random.randint(-s.refresh_jitter_seconds, s.refresh_jitter_seconds)
                    logger.info(
                        "[refresh] TTL：%d 秒 | 计划刷新：%s | 阈值：%d 秒 | jitter：%+d 秒 | 立即刷新",
                        ttl, datetime.datetime.now().strftime("%H:%M:%S"),
                        s.refresh_ttl_threshold, jitter,
                    )
                    req_id = _pre_refresh_diag(ttl)
                    await self._do_refresh(req_id=req_id)
                    _post_refresh_success()
                    stats["total"] += 1
                    stats["success"] += 1
                    stats["consecutive"] += 1
                    logger.info("[refresh][%s] 连续成功：%d 次", req_id, stats["consecutive"])
                    fail_count = 0
                    _hourly_stats_check()
                    continue  # 刷新成功后重新调度

                # Token 充足 → 计算睡眠时间并等待
                jitter = random.randint(-s.refresh_jitter_seconds, s.refresh_jitter_seconds)
                threshold_with_jitter = s.refresh_ttl_threshold + jitter
                sleep_seconds = max(1, ttl - threshold_with_jitter)

                now = datetime.datetime.now()
                next_refresh = now + datetime.timedelta(seconds=sleep_seconds)
                logger.info(
                    "[refresh] JWT 剩余：%d 秒 | 计划刷新：%s | 随机偏移：%+d 秒 | 睡眠 %d 秒",
                    ttl, next_refresh.strftime("%H:%M:%S"), jitter, sleep_seconds,
                )
                fail_count = 0
                _hourly_stats_check()
                sleep_start = time.monotonic()
                await asyncio.sleep(sleep_seconds)
                elapsed = time.monotonic() - sleep_start
                # 检测系统休眠恢复：实际耗时远超预期
                if elapsed > sleep_seconds + 30:
                    ttl = auth.token_ttl_seconds()
                    logger.warning(
                        "[refresh] 检测到系统休眠恢复（预期睡眠 %d 秒，实际 %d 秒），重新计算 Token TTL：%d 秒",
                        sleep_seconds, int(elapsed), ttl,
                    )
                    if ttl <= 0:
                        logger.critical(
                            "[refresh] Token 已过期，请使用 /gta 更新ck 重新注入 Cookie。"
                        )
                    elif ttl <= s.refresh_ttl_threshold:
                        logger.info("[refresh] TTL 已进入刷新窗口（%d 秒），立即刷新", ttl)
                continue  # 睡醒后重新检查 TTL

            except AuthError as e:
                _post_refresh_fail("429" if "429" in str(e) else str(e)[:80])
                stats["total"] += 1
                stats["consecutive"] = 0
                err = str(e)
                if "429" in err:
                    stats["http_429"] += 1
                    throttled = True
                    now = datetime.datetime.now()
                    resume_at = now + datetime.timedelta(minutes=s.throttle_pause_minutes)
                    logger.warning(
                        "[refresh][%s] ✗ 429 限速 | 暂停至：%s | %s",
                        _current_req_id, resume_at.strftime("%H:%M:%S"), _stats_line(),
                    )
                    await asyncio.sleep(s.throttle_pause_minutes * 60)
                    throttled = False
                    fail_count = 0
                    continue

                # 401 / 403 / token 过期等不可恢复的认证错误
                stats["fail"] += 1
                fail_count += 1
                logger.warning(
                    "[refresh][%s] ✗ 续期失败 (连续 %d 次) — %s | %s",
                    _current_req_id, fail_count, e, _stats_line(),
                )
                if fail_count >= s.throttle_max_fail_count:
                    logger.critical(
                        "[refresh] 连续 %d 次续期失败，尝试浏览器自动登录恢复…",
                        fail_count,
                    )
                    try:
                        from .services import login as _login
                        await _login.recover()
                        logger.info("[refresh] 浏览器自动登录成功，恢复续期")
                        fail_count = 0
                        continue
                    except Exception as le:
                        logger.critical(
                            "[refresh] 浏览器登录也失败: %s，请使用 /gta 更新ck 重新注入 Cookie。",
                            le,
                        )

            except Exception as e:
                stats["total"] += 1
                stats["consecutive"] = 0
                if self._is_network_error(e):
                    is_timeout = any(
                        kw in str(e).lower()
                        for kw in ['timeout', 'timed out']
                    )
                    http_info = "Timeout" if is_timeout else "Network Error"
                    _post_refresh_fail(http_info)
                    stats["fail"] += 1
                    if is_timeout:
                        stats["timeout"] += 1
                    ttl = auth.token_ttl_seconds()
                    # 网络异常退避重试
                    if ttl < s.net_retry_min_ttl:
                        logger.error(
                            "[refresh][%s] ✗ 网络异常 | TTL 仅剩 %d 秒（< %d），放弃重试 | %s | %s",
                            _current_req_id, ttl, s.net_retry_min_ttl, e, _stats_line(),
                        )
                        fail_count += 1
                    else:
                        delays = s.net_retry_delays
                        retry_index = min(fail_count, len(delays) - 1)
                        backoff = delays[retry_index]
                        fail_count += 1
                        logger.warning(
                            "[refresh][%s] ✗ 网络异常 | TTL：%d 秒 | 重试：第 %d 次%s | %s",
                            _current_req_id, ttl, fail_count,
                            "（timeout）" if is_timeout else "",
                            e,
                        )
                        await asyncio.sleep(backoff)
                        # 重试前重新计算 TTL（timeout/backoff 已消耗时间）
                        ttl = auth.token_ttl_seconds()
                        logger.info("[refresh] retry 前重新计算 TTL：%d 秒", ttl)
                        if ttl <= 0:
                            logger.critical(
                                "[refresh] Token 已过期，请使用 /gta 更新ck 重新注入 Cookie。"
                            )
                            # 不 continue，走底部退避轮询
                        elif ttl < s.net_retry_min_ttl:
                            logger.warning(
                                "[refresh] TTL 已不足 %d 秒（%d 秒），取消网络重试",
                                s.net_retry_min_ttl, ttl,
                            )
                            # 不 continue，走底部退避轮询
                        else:
                            continue  # TTL 仍充足，立即重试
                else:
                    _post_refresh_fail("Unknown")
                    stats["fail"] += 1
                    fail_count += 1
                    logger.error(
                        "[refresh][%s] 未知异常 (连续 %d 次) — %s | %s",
                        _current_req_id, fail_count, e, _stats_line(),
                    )
                    if fail_count >= s.throttle_max_fail_count:
                        logger.critical(
                            "[refresh] 连续 %d 次续期失败，尝试浏览器自动登录恢复…",
                            fail_count,
                        )
                        try:
                            from .services import login as _login
                            await _login.recover()
                            logger.info("[refresh] 浏览器自动登录成功，恢复续期")
                            fail_count = 0
                            continue
                        except Exception as le:
                            logger.critical(
                                "[refresh] 浏览器登录也失败: %s，请使用 /gta 更新ck 重新注入 Cookie。",
                                le,
                            )

            # 错误路径到达此处（AuthError 或非网络异常）→ 退避轮询
            ttl = auth.token_ttl_seconds()
            if ttl > 0:
                backoff = min(s.refresh_check_interval, ttl)
            else:
                backoff = s.refresh_check_interval
            await asyncio.sleep(backoff)

    async def _query_player(self, nickname: str, persist: bool = True, force: bool = False, timeout: int = 60):
        """内嵌查询：直接调用本地 services，不再走 HTTP。返回 dict（含异常检测）。

        抛出 ScapiError("玩家不存在") / AuthError / WebStatsError 等。
        """
        import dataclasses
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: query_svc.query_player(nickname, persist=not force and persist, timeout=timeout))
        body = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result
        # 异常检测
        try:
            from .services import judgement
            player_data = {
                "profile": body.get("profile") or {},
                "overview": body.get("overview") or {},
                "stats": body.get("stats") or {},
            }
            awards_data = None
            try:
                awards_data = await self._query_awards(nickname, persist=not force)
            except Exception:
                pass
            judge_findings = await loop.run_in_executor(None, judgement.check, player_data, awards_data)
            body["judgements"] = [{"level": lv, "message": msg} for lv, msg in judge_findings]
        except Exception:
            pass
        return body

    async def _query_awards(self, nickname: str, category: str = "", persist: bool = True, timeout: int = 60):
        """内嵌奖章查询。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: query_svc.query_awards(nickname, category=category, persist=persist, timeout=timeout))

    async def _persist_auth_state(self, authorization: str, refresh_cookies: dict[str, str]) -> None:
        """已废弃，保留兼容。"""
        pass

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

        # 自建 API（240项深度数据）
        try:
            body = await self._query_player(nickname)
            lines.append("\n生涯信息")
            lines.append(format_career_text(body))
        except ScapiError:
            lines.append(f"\n❌ 玩家不存在: {nickname}")
        except AuthError:
            lines.append("\n❌ 凭证过期，请用 /gta 更新ck 重新注入 Cookie")
        except Exception as e:
            logger.warning("[gta_online_helper] 查询失败: %s", e)
            lines.append(f"\n查询失败: {e}")

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

        try:
            body = await self._query_player(target, force=force)
            yield event.plain_result(format_career_text(body))
        except ScapiError:
            yield event.plain_result("❌ 玩家不存在")
        except AuthError:
            yield event.plain_result("❌ 凭证过期，请用 /gta 更新ck 重新注入 Cookie")
        except Exception as e:
            logger.warning("[gta_online_helper] 查询失败: %s", e)
            yield event.plain_result(f"查询失败: {e}")

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
            "\n数据源: 自建API"
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

        # Remove command prefix (更新ck / setck / ck)
        for prefix in ("更新ck ", "更新CK ", "setck ", "ck "):
            if payload.startswith(prefix):
                payload = payload[len(prefix):].strip()
                break

        # Strip adapter-appended message markers, e.g. [MSG_ID:1211303900] / [MSGID:1211303900].
        payload = re.sub(r"\s*\[(?:MSG[_ ]?ID)\s*:\s*\d+\]\s*$", "", payload, flags=re.IGNORECASE)
        payload = payload.strip()

        if not payload or not payload.strip():
            yield event.plain_result("用法: /gta 更新ck <BearerToken 或完整Cookie字符串>")
            return

        payload = payload.strip()
        if "=" in payload:
            parsed = auth.parse_cookie_string(payload)
            if not parsed:
                yield event.plain_result("CK 解析失败，请检查格式，例如: key=1;key2=2")
                return

            missing_fields = [field for field in REQUIRED_COOKIE_FIELDS if not parsed.get(field)]
            if missing_fields:
                yield event.plain_result(
                    f"CK 缺少必需字段: {', '.join(missing_fields)}"
                )
                return

            parsed = auth.update_from_cookie_string(payload)

            # 同时写入 browser profile（用于后续快速恢复）
            try:
                from .services import login as _login
                await _login.seed_browser_profile(payload)
            except Exception:
                pass

            # Persist to AstrBot KV storage
            await self.put_kv_data(REFRESH_COOKIES_KV_KEY, parsed)

            token = parsed.get("BearerToken", "").strip()
            if token:
                await self.put_kv_data(AUTHORIZATION_KV_KEY, token)
                # 立即刷新一次，趁 token 还有效
                asyncio.create_task(self._try_immediate_refresh())
                masked = f"{token[:8]}..." if len(token) > 8 else "***"
                yield event.plain_result(
                    f"Cookie 已更新，Authorization: {masked}，正在续期..."
                )
            else:
                yield event.plain_result("Cookie 已缓存（未检测到 BearerToken）。")
            return

        authorization = payload
        auth.set_authorization(authorization)
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

        try:
            body = await self._query_player(target, force=force)
            yield event.plain_result(format_career_text(body))
        except ScapiError:
            yield event.plain_result("❌ 玩家不存在")
        except AuthError:
            yield event.plain_result("❌ 凭证过期，请用 /gta 更新ck 重新注入 Cookie")
        except Exception as e:
            logger.warning("[gta_online_helper] 查询失败: %s", e)
            yield event.plain_result(f"查询失败: {e}")

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
            body = await self._query_player(target)
            text = format_category_text(body, cat_key)
            yield event.plain_result(text)
        except Exception:
            yield event.plain_result(f"查询失败，API 服务离线或 token 过期。")

    @filter.command("查战斗")
    async def gta_combat(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查战斗 <昵称>")
            return
        try:
            body = await self._query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "combat"))
        except Exception:
            yield event.plain_result("查询失败")

    @filter.command("查犯罪")
    async def gta_crimes(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查犯罪 <昵称>")
            return
        try:
            body = await self._query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "crimes"))
        except Exception:
            yield event.plain_result("查询失败")

    @filter.command("查载具")
    async def gta_vehicles(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查载具 <昵称>")
            return
        try:
            body = await self._query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "vehicles"))
        except Exception:
            yield event.plain_result("查询失败")

    @filter.command("查收支")
    async def gta_cash(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查收支 <昵称>")
            return
        try:
            body = await self._query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "cash"))
        except Exception:
            yield event.plain_result("查询失败")

    @filter.command("查技能")
    async def gta_skills(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查技能 <昵称>")
            return
        try:
            body = await self._query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "skills"))
        except Exception:
            yield event.plain_result("查询失败")

    @filter.command("查武器")
    async def gta_weapons(self, event: AstrMessageEvent, nickname: str = ""):
        if not nickname.strip():
            yield event.plain_result("用法: 查武器 <昵称>")
            return
        try:
            body = await self._query_player(nickname.strip())
            yield event.plain_result(format_category_text(body, "weapons"))
        except Exception:
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
            b1, b2 = await self._query_player(n1), await self._query_player(n2)
            yield event.plain_result(format_compare_text(b1, n1, b2, n2))
        except Exception as e:
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
                body = await self._query_awards(keyword)
                yield event.plain_result(format_awards_text(body))
                return
            except Exception as e:
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
                body = await self._query_awards(target)
                items = body.get("awards", {}).get("_items", [])
                for n, m, d, t in items:
                    player_progress[n.lower()] = (m, d, t)
            except Exception:
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

    @filter.command("查车")
    async def cmd_vehicle_search(self, event: AstrMessageEvent, keyword: str = ""):
        """查车 <关键词> — 搜索载具百科（支持中英文/拼音/品牌/型号）"""
        kw = keyword.strip()
        if not kw:
            parts = str(event.message_str or "").strip().split(maxsplit=1)
            if len(parts) > 1:
                kw = parts[1].strip()
        if not kw:
            yield event.plain_result("用法: 查车 <关键词>\n支持: 车名/品牌/型号/拼音\n示例: 查车 猛牛  |  查车 pegassi")
            return
        results = search_vehicles(kw)
        yield event.plain_result(format_vehicle_list(results, kw))

    @filter.command("查车详情")
    async def cmd_vehicle_detail(self, event: AstrMessageEvent, name: str = ""):
        """查车详情 <车名> — 查看载具完整信息+缩略图"""
        n = name.strip()
        if not n:
            parts = str(event.message_str or "").strip().split(maxsplit=1)
            if len(parts) > 1:
                n = parts[1].strip()
        if not n:
            yield event.plain_result("用法: 查车详情 <车名>\n示例: 查车详情 猛牛 STX 追逐")
            return
        v = get_vehicle_by_name(n)
        if not v:
            results = search_vehicles(n, limit=1)
            if results:
                v = results[0]
            else:
                yield event.plain_result(f"未找到「{n}」。试试「查车 {n}」模糊搜索？")
                return
        text = format_vehicle_detail(v)
        plugin_dir = os.path.dirname(__file__)
        chain = [Plain(text)]
        thumb = get_thumbnail(v)
        if thumb:
            chain.append(Image.fromURL(thumb))
        yield event.chain_result(chain)

    @filter.command("查品牌")
    async def cmd_vehicle_brand(self, event: AstrMessageEvent, brand: str = ""):
        """查品牌 <品牌名> — 列出该品牌所有载具"""
        b = brand.strip()
        if not b:
            parts = str(event.message_str or "").strip().split(maxsplit=1)
            if len(parts) > 1:
                b = parts[1].strip()
        if not b:
            yield event.plain_result("用法: 查品牌 <品牌名>\n示例: 查品牌 佩嘉西  |  查品牌 pegassi")
            return
        vehicles = get_vehicles_by_brand(b)
        yield event.plain_result(format_brand_list(b, vehicles))

    @filter.command("查颜色")
    async def cmd_color_search(self, event: AstrMessageEvent, keyword: str = ""):
        """查颜色 <色名/分类/分类+色名> — 支持简称如 金属红/工业蓝/chrome"""
        kw = keyword.strip()
        if not kw:
            parts = str(event.message_str or "").strip().split(maxsplit=1)
            if len(parts) > 1:
                kw = parts[1].strip()
        if not kw:
            yield event.plain_result("查颜色 <关键词>\n颜色名: 红/深蓝/都灵红\n分类: 金属质感/工业/哑光/铬合金\n分类+颜色: 金属红/工业蓝/哑光黑\n简称: 金属→金属质感")
            return

        def _show_detail(c):
            chain = [Plain(format_color_detail(c))]
            img = get_color_image(c)
            if img:
                chain.append(Image.fromURL(img))
            return chain

        # 数字 → ID
        if kw.isdigit():
            c = get_color_by_id(int(kw))
            if c:
                yield event.chain_result(_show_detail(c))
                return

        # 搜索
        results = search_colors(kw)
        if not results:
            yield event.plain_result(f"未找到「{kw}」相关颜色。试试 红/蓝/金属质感/工业？")
        elif len(results) == 1:
            yield event.chain_result(_show_detail(results[0]))
        else:
            yield event.plain_result(format_color_list(results, kw))

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
            "\n=== 载具百科 ===\n"
            "查车 <关键词> — 搜索载具（名称/品牌/型号/拼音）\n"
            "查车详情 <车名> — 载具完整信息+图片\n"
            "查品牌 <品牌> — 列出品牌所有载具\n"
            "查颜色 <关键词/ID> — 搜索颜色（名称/分类/HEX）\n"
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
        if hasattr(self, '_refresh_task') and self._refresh_task:
            self._refresh_task.cancel()
