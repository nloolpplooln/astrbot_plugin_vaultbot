"""集中配置：通过环境变量覆盖默认值。"""
from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    # ── 缓存 ──
    cache_ttl_seconds: int = 14400          # 玩家数据缓存 4 小时
    http_timeout: int = 60                   # 查询超时
    http_retries: int = 2
    http_proxy: str = ""
    db_path: str = "socialclub.db"

    # ── Token 刷新 ──
    refresh_check_interval: int = 60        # 异常退避轮询间隔（秒）
    refresh_ttl_threshold: int = 60         # TTL 低于此值才续期（秒），实际 = 阈值 ± jitter
    refresh_jitter_seconds: int = 5         # 随机抖动范围（±秒），避免固定节奏
    refresh_timeout: int = 15               # refreshaccess 超时
    session_refresh_timeout: int = 15       # CK 续期请求超时

    # ── 网络异常重试 ──
    net_retry_delays: list = [2, 5]         # 第1次/第2次重试间隔（秒），之后沿用最后值
    net_retry_min_ttl: int = 15             # TTL 低于此值不再重试

    # ── 限速处理 ──
    throttle_pause_minutes: int = 15        # 429 后暂停时长
    throttle_max_fail_count: int = 3        # 连续失败多少次报 CRITICAL

    # ── R* 凭证 ──
    rsc_email: str = ""                     # 自动登录用（预留）
    rsc_password: str = ""

    def __init__(self):
        self.cache_ttl_seconds = int(os.getenv("CACHE_TTL_SECONDS", str(self.cache_ttl_seconds)))
        self.http_timeout = int(os.getenv("HTTP_TIMEOUT", str(self.http_timeout)))
        self.http_retries = int(os.getenv("HTTP_RETRIES", str(self.http_retries)))
        self.http_proxy = os.getenv("HTTP_PROXY", self.http_proxy)
        self.db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "socialclub.db"))

        self.refresh_check_interval = int(os.getenv("REFRESH_CHECK_INTERVAL", str(self.refresh_check_interval)))
        self.refresh_ttl_threshold = int(os.getenv("REFRESH_TTL_THRESHOLD", str(self.refresh_ttl_threshold)))
        self.refresh_jitter_seconds = int(os.getenv("REFRESH_JITTER_SECONDS", str(self.refresh_jitter_seconds)))
        self.refresh_timeout = int(os.getenv("REFRESH_TIMEOUT", str(self.refresh_timeout)))
        self.session_refresh_timeout = int(os.getenv("SESSION_REFRESH_TIMEOUT", str(self.session_refresh_timeout)))

        self.net_retry_min_ttl = int(os.getenv("NET_RETRY_MIN_TTL", str(self.net_retry_min_ttl)))

        self.throttle_pause_minutes = int(os.getenv("THROTTLE_PAUSE_MINUTES", str(self.throttle_pause_minutes)))
        self.throttle_max_fail_count = int(os.getenv("THROTTLE_MAX_FAIL_COUNT", str(self.throttle_max_fail_count)))

        self.rsc_email = os.getenv("RSC_EMAIL", "")
        self.rsc_password = os.getenv("RSC_PASSWORD", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
