"""Playwright 浏览器自动登录 R* Social Club。

用于 Token 彻底失效时（refreshaccess 拒收）的灾难恢复。
正常运行时仍用 curl_cffi 轻量续期，不走浏览器。

策略：
1. 首次登录 → 保存浏览器状态（userDataDir）
2. 后续恢复 → 加载已保存状态 → 大概率免验证码
3. 如果遇到 hCaptcha → 截图保存，通知用户手动处理
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Dict, Optional, Tuple

from . import auth


def _get_logger():
    """延迟导入 logger，避免 standalone 进程中的 Pydantic 字节码崩溃。"""
    try:
        from astrbot.api import logger as _logger
        return _logger
    except Exception:
        import logging
        return logging.getLogger("login")

logger = _get_logger()

# ── 路径 ──
_PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
_USER_DATA_DIR = os.path.join(_PLUGIN_DIR, "data", "browser_profile")
_SCREENSHOT_DIR = os.path.join(_PLUGIN_DIR, "data", "screenshots")

SIGNIN_URL = "https://signin.rockstargames.com/signin/user-form?cid=socialclub"
SC_URL = "https://socialclub.rockstargames.com/"

# 登录凭据（从环境变量读取）
RSC_EMAIL = os.getenv("RSC_EMAIL", "")
RSC_PASSWORD = os.getenv("RSC_PASSWORD", "")

# 关键 Cookie 名
TARGET_COOKIES = [
    "BearerToken",
    "TS01008f56", "TS011be943", "TS01347d69",
    "RockStarWebSessionId", "prod",
    "CSRFToken", "AutoLoginCheck",
]


async def _ensure_dirs():
    os.makedirs(_USER_DATA_DIR, exist_ok=True)
    os.makedirs(_SCREENSHOT_DIR, exist_ok=True)


class LoginError(RuntimeError):
    """自动登录失败。"""


def _is_logged_in_via_cookies() -> bool:
    """检查当前 auth 模块的 Cookie 是否齐全且 Token 有效。"""
    if auth.missing_refresh_keys():
        return False
    return auth.token_ttl_seconds() > 0


async def _extract_cookies_from_page(page) -> Dict[str, str]:
    """从 Playwright page 提取目标 Cookie。"""
    all_cookies = await page.context.cookies()
    extracted: Dict[str, str] = {}
    for c in all_cookies:
        if c["name"] in TARGET_COOKIES or c["name"].startswith("TS01"):
            extracted[c["name"]] = c["value"]
    # 也提取所有 cookie（覆盖非目标但有用的）
    for c in all_cookies:
        if c["name"] not in extracted:
            extracted[c["name"]] = c["value"]
    return extracted


async def _feed_cookies_to_auth(cookies: Dict[str, str]):
    """将浏览器提取的 Cookie 注入 auth 模块。"""
    if not cookies:
        return
    # 构建 cookie 字符串
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    auth.update_from_cookie_string(cookie_str)
    logger.info(
        "[login] 已注入 %d 个 Cookie (BearerToken=%s, TTL=%ds)",
        len(cookies),
        "有" if cookies.get("BearerToken") else "无",
        auth.token_ttl_seconds(),
    )


async def seed_browser_profile(cookie_string: str) -> bool:
    """用真实浏览器导出的 Cookie 初始化 browser profile。

    用户只需从自己的浏览器导出一份完整 Cookie（含 TS* + BearerToken），
    调用此函数写入 profile，之后快速恢复即可免登录。

    方法：
    1. 浏览器 F12 → Application → Cookies → 全选复制所有 Cookie
    2. 用 /gta 更新ck 注入（这同时也会写入 browser profile）
    """
    await _ensure_dirs()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    parsed = auth.parse_cookie_string(cookie_string)
    if not parsed:
        return False

    # 转换为 Playwright cookie 格式
    pw_cookies = []
    for name, value in parsed.items():
        pw_cookies.append({
            "name": name,
            "value": value,
            "domain": ".rockstargames.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })

    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                _USER_DATA_DIR,
                headless=True,
            )
            await context.add_cookies(pw_cookies)
            await context.close()
        logger.info("[login] Browser profile seeded with %d cookies", len(pw_cookies))
        return True
    except Exception as e:
        logger.warning("[login] Failed to seed browser profile: %s", e)
        return False
    """尝试用浏览器快速恢复 Token（不需登录，只需访问页面利用滑动过期）。

    加载已保存的浏览器 profile，打开 R* 首页，提取更新的 Cookie。
    """
    await _ensure_dirs()

    if not os.path.isdir(_USER_DATA_DIR) or not os.listdir(_USER_DATA_DIR):
        logger.info("[login] 无浏览器 profile，跳过快速恢复")
        return False

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("[login] playwright 未安装")
        return False

    logger.info("[login] 尝试浏览器快速恢复（加载已保存 profile）…")

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                _USER_DATA_DIR,
                headless=True,
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
        except Exception as e:
            logger.warning("[login] 启动浏览器失败: %s", e)
            return False

        try:
            page = await context.new_page()

            # 访问首页 → 滑动续期
            await page.goto(SC_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)  # 等待 JS 执行（可能触发 refreshaccess）

            # 再访问 jobs 页面续 CK
            await page.goto(
                SC_URL + "jobs?dateRangeCreated=any&filter=me&sort=likes&title=gtav",
                wait_until="domcontentloaded", timeout=30000,
            )
            await asyncio.sleep(3)

            cookies = await _extract_cookies_from_page(page)
            await _feed_cookies_to_auth(cookies)

            ttl = auth.token_ttl_seconds()
            logger.info("[login] 快速恢复完成，TTL=%ds", ttl)
            await context.close()
            return ttl > 0

        except Exception as e:
            logger.warning("[login] 快速恢复失败: %s", e)
            await context.close()
            return False


async def full_login() -> bool:
    """完整登录流程：打开浏览器 → 填表 → 登录 → 提取 Cookie。

    如果遇到验证码，截图保存并抛 LoginError。
    """
    await _ensure_dirs()

    email = RSC_EMAIL
    password = RSC_PASSWORD
    if not email or not password:
        raise LoginError("未配置 RSC_EMAIL / RSC_PASSWORD 环境变量")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise LoginError("playwright 未安装，请先 pip install playwright && python -m playwright install chromium")

    logger.info("[login] 开始完整登录流程…")

    async with async_playwright() as p:
        # 使用持久化 context 保存登录状态
        context = await p.chromium.launch_persistent_context(
            _USER_DATA_DIR,
            headless=True,
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        try:
            page = await context.new_page()

            # ── 步骤 1：检查是否已登录 ──
            await page.goto(SC_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            current_url = page.url
            logger.info("[login] 当前 URL: %s", current_url[:80])

            # 检查是否停留在 socialclub（已登录）还是被重定向
            is_on_socialclub = "socialclub.rockstargames.com" in current_url
            is_on_signin = "signin" in current_url.lower()

            if is_on_socialclub:
                # 停留在 socialclub → 可能已登录，检查 Cookie 完整性
                cookies = await _extract_cookies_from_page(page)
                missing = [k for k in auth.REQUIRED_COOKIE_KEYS if not cookies.get(k)]
                if not missing:
                    logger.info("[login] 已登录状态有效（%d 个 Cookie），提取中…", len(cookies))
                    await _feed_cookies_to_auth(cookies)
                    await context.close()
                    return _is_logged_in_via_cookies()
                else:
                    logger.info("[login] 在 socialclub 但缺少必需 Cookie: %s，尝试登录…", missing)
                    # 继续走登录流程 — 导航到 signin 页
                    await page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)
                    is_on_signin = True

            if not is_on_signin:
                # 被重定向到其他页面（如 www.rockstargames.com）→ 未登录
                logger.info("[login] 未登录（被重定向到 %s），打开登录页…", current_url[:60])
                await page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

            # ── 步骤 2：填写邮箱 + 密码（同一页面）──
            logger.info("[login] 填写邮箱…")
            email_input = await _wait_for_input(page, ["input[name='email']", "input[type='email']"], timeout=15)
            if not email_input:
                await _save_debug_screenshot(page)
                await _handle_captcha_if_present(page)
                raise LoginError("找不到邮箱输入框，页面结构可能有变")

            await email_input.fill(email)
            await asyncio.sleep(0.5)

            logger.info("[login] 填写密码…")
            pwd_input = await _wait_for_input(page, ["input[name='password']", "input[type='password']"], timeout=10)
            if not pwd_input:
                await _save_debug_screenshot(page)
                raise LoginError("找不到密码输入框，页面结构可能有变")

            await pwd_input.fill(password)
            await asyncio.sleep(0.5)

            # ── 步骤 3：检查验证码 ──
            await _handle_captcha_if_present(page)

            # ── 步骤 4：点击 Sign In ──
            logger.info("[login] 点击登录按钮…")
            signin_btn = await _find_clickable(page, [
                "button:has-text('Sign in')",
                "button:has-text('Sign In')",
                "button:has-text('登录')",
                "button[type='submit']",
                "input[type='submit']",
            ])
            if signin_btn:
                await signin_btn.click()
            else:
                await pwd_input.press("Enter")
            await asyncio.sleep(5)

            # ── 步骤 5：检查验证码（登录后可能出现） ──
            await _handle_captcha_if_present(page)

            # ── 步骤 6：检查邮箱验证码 ──
            await _handle_email_verification(page)

            # ── 步骤 7：等待跳转 ──
            for i in range(10):
                await asyncio.sleep(2)
                current_url = page.url
                if "socialclub" in current_url and "signin" not in current_url:
                    logger.info("[login] 登录成功！当前 URL: %s", current_url[:80])
                    break
                if "signin" not in current_url:
                    break
            else:
                logger.warning("[login] 可能仍在登录页，尝试提取已有 Cookie…")

            # ── 步骤 8：提取 Cookie ──
            await asyncio.sleep(2)
            cookies = await _extract_cookies_from_page(page)
            await _feed_cookies_to_auth(cookies)

            await context.close()
            return _is_logged_in_via_cookies()

        except LoginError:
            await context.close()
            raise
        except Exception as e:
            logger.error("[login] 登录异常: %s", e)
            await _save_debug_screenshot(page)
            await context.close()
            raise LoginError(f"登录失败: {e}")


async def _wait_for_input(page, selectors: list[str], timeout: int = 15):
    """等待任一选择器匹配的输入框出现。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        await asyncio.sleep(0.5)
    return None


async def _find_clickable(page, selectors: list[str]):
    """查找第一个可点击的元素。"""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return el
        except Exception:
            continue
    return None


async def _handle_captcha_if_present(page) -> bool:
    """检查是否有 hCaptcha，有则截图保存。"""
    captcha_selectors = [
        "iframe[src*='hcaptcha']",
        "iframe[src*='captcha']",
        "div.h-captcha",
        "[data-hcaptcha-widget-id]",
        "#hcaptcha-container",
    ]
    for sel in captcha_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await _save_debug_screenshot(page)
                raise LoginError(
                    "检测到验证码(hCaptcha)。请手动在浏览器中完成验证码后重试。\n"
                    f"截图已保存至: {_SCREENSHOT_DIR}\n"
                    f"方法: 用桌面浏览器打开 {_USER_DATA_DIR} 对应的 profile，\n"
                    f"访问 socialclub.rockstargames.com 手动登录一次后，"
                    f"回到此处重新运行。"
                )
        except LoginError:
            raise
        except Exception:
            continue
    return False


async def _handle_email_verification(page):
    """处理邮箱验证码页面。"""
    code_selectors = [
        "input[name='code']",
        "input[placeholder*='code']",
        "input[placeholder*='验证']",
        "input[type='text']",
    ]
    for sel in code_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                logger.warning("[login] 检测到邮箱验证码页面，需要手动输入验证码")
                await _save_debug_screenshot(page)
                raise LoginError(
                    "需要邮箱验证码。请检查邮箱 nloolpplooln@gmzil.com 获取验证码。\n"
                    "验证码通常 6 位数字。收到后请手动在浏览器中完成验证。"
                )
        except LoginError:
            raise
        except Exception:
            continue


async def _save_debug_screenshot(page):
    """保存调试截图。"""
    try:
        timestamp = int(time.time())
        path = os.path.join(_SCREENSHOT_DIR, f"login_debug_{timestamp}.png")
        await page.screenshot(path=path, full_page=True)
        logger.info("[login] 调试截图已保存: %s", path)
    except Exception as e:
        logger.warning("[login] 截图失败: %s", e)


async def recover() -> bool:
    """Token 失效时的自动恢复入口。

    顺序：
    1. 快速恢复（已有 browser profile → 访问页面刷新 Cookie）
    2. 完整登录（填表 → 登录 → 提取 Cookie）
    3. 如果都失败 → 抛 LoginError 通知用户
    """
    logger.info("[login] 开始自动恢复…")

    # 先试快速恢复
    if await try_quick_restore():
        logger.info("[login] 快速恢复成功")
        return True

    # 再试完整登录
    logger.info("[login] 快速恢复失败，尝试完整登录…")
    if await full_login():
        logger.info("[login] 完整登录成功")
        return True

    raise LoginError("自动恢复失败，请手动注入 Cookie。")
