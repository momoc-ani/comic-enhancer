from __future__ import annotations

from collections.abc import Callable
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import fnmatch
import logging
import os
import re
import threading
import time
from urllib.parse import urlparse

import httpx

from .logging_utils import log_operation


logger = logging.getLogger(__name__)


WINHTTP_ACCESS_TYPE_NO_PROXY = 1
WINHTTP_ACCESS_TYPE_NAMED_PROXY = 3
WINHTTP_AUTOPROXY_AUTO_DETECT = 0x00000001
WINHTTP_AUTOPROXY_CONFIG_URL = 0x00000002
WINHTTP_AUTO_DETECT_TYPE_DHCP = 0x00000001
WINHTTP_AUTO_DETECT_TYPE_DNS_A = 0x00000002


@dataclass(frozen=True)
class WindowsProxyConfig:
    """描述当前 Windows 用户的 WinINET/WinHTTP 代理策略。"""

    auto_detect: bool = False
    auto_config_url: str = ""
    proxy: str = ""
    bypass: str = ""

    @property
    def enabled(self) -> bool:
        """判断当前系统是否启用了任一种代理策略。"""
        return bool(self.auto_detect or self.auto_config_url or self.proxy)


@dataclass(frozen=True)
class ProxyDecision:
    """描述单个目标 URL 最终采用的代理或直连决策。"""

    source: str
    proxy_url: str | None = None

    @property
    def proxied(self) -> bool:
        """判断当前决策是否会通过代理连接。"""
        return self.proxy_url is not None


class _CurrentUserProxyConfig(ctypes.Structure):
    """映射 WINHTTP_CURRENT_USER_IE_PROXY_CONFIG。"""

    _fields_ = [
        ("auto_detect", wintypes.BOOL),
        ("auto_config_url", ctypes.c_void_p),
        ("proxy", ctypes.c_void_p),
        ("bypass", ctypes.c_void_p),
    ]


class _AutoProxyOptions(ctypes.Structure):
    """映射 WINHTTP_AUTOPROXY_OPTIONS。"""

    _fields_ = [
        ("flags", wintypes.DWORD),
        ("auto_detect_flags", wintypes.DWORD),
        ("auto_config_url", wintypes.LPCWSTR),
        ("reserved_pointer", ctypes.c_void_p),
        ("reserved", wintypes.DWORD),
        ("auto_logon", wintypes.BOOL),
    ]


class _ProxyInfo(ctypes.Structure):
    """映射 WINHTTP_PROXY_INFO。"""

    _fields_ = [
        ("access_type", wintypes.DWORD),
        ("proxy", ctypes.c_void_p),
        ("bypass", ctypes.c_void_p),
    ]


ConfigReader = Callable[[], WindowsProxyConfig]
AutoProxyResolver = Callable[[str, WindowsProxyConfig], str | None]


class WindowsSystemProxyResolver:
    """按目标 URL 解析 Windows 手动代理、PAC、WPAD 和绕过规则。"""

    # 方法说明：初始化平台判断和可替换的 Windows API 读取函数。
    def __init__(
        self,
        *,
        platform_name: str | None = None,
        config_reader: ConfigReader | None = None,
        auto_proxy_resolver: AutoProxyResolver | None = None,
    ):
        self.platform_name = platform_name or os.name
        self.config_reader = config_reader or _read_windows_proxy_config
        self.auto_proxy_resolver = auto_proxy_resolver or _resolve_windows_auto_proxy

    @property
    def is_windows(self) -> bool:
        """判断当前解析器是否运行在 Windows 策略模式。"""
        return self.platform_name == "nt"

    # 方法说明：按当前系统策略为单个 URL 返回代理或直连决策。
    def resolve(self, url: str) -> ProxyDecision:
        if not self.is_windows:
            return ProxyDecision(source="environment")
        try:
            config = self.config_reader()
        except OSError as error:
            self._log_failure(url, "config", error)
            return ProxyDecision(source="windows_config_error")
        if not config.enabled:
            return ProxyDecision(source="windows_disabled")

        automatic_configs: list[tuple[str, WindowsProxyConfig]] = []
        if config.auto_config_url:
            automatic_configs.append(
                (
                    "pac",
                    WindowsProxyConfig(
                        auto_config_url=config.auto_config_url,
                        proxy=config.proxy,
                        bypass=config.bypass,
                    ),
                )
            )
        if config.auto_detect:
            automatic_configs.append(
                (
                    "wpad",
                    WindowsProxyConfig(
                        auto_detect=True,
                        proxy=config.proxy,
                        bypass=config.bypass,
                    ),
                )
            )
        for source, automatic_config in automatic_configs:
            try:
                proxy_spec = self.auto_proxy_resolver(url, automatic_config)
            except OSError as error:
                self._log_failure(url, source, error)
                continue
            return ProxyDecision(
                source=source,
                proxy_url=_select_proxy_url(proxy_spec or "", url),
            )

        if config.proxy:
            if _matches_bypass(url, config.bypass):
                return ProxyDecision(source="manual_bypass")
            return ProxyDecision(
                source="manual",
                proxy_url=_select_proxy_url(config.proxy, url),
            )
        return ProxyDecision(source="windows_direct")

    # 方法说明：记录 Windows 系统代理读取或 PAC 求值失败。
    @staticmethod
    def _log_failure(url: str, stage: str, error: OSError) -> None:
        log_operation(
            logger,
            logging.WARNING,
            feature="Windows系统代理解析",
            parameters={"host": urlparse(url).hostname or "", "stage": stage},
            result={"status": "failed", "error": type(error).__name__},
        )


# 方法说明：按目标 URL 返回并记录外部请求的系统代理决策。
def resolve_external_proxy(
    url: str,
    *,
    resolver: WindowsSystemProxyResolver | None = None,
) -> ProxyDecision:
    started = time.perf_counter()
    active_resolver = resolver or _default_system_proxy_resolver
    decision = active_resolver.resolve(url)
    _log_proxy_decision(url, decision, started)
    return decision


# 方法说明：创建遵循 Windows 系统策略的外部 HTTP 客户端。
def external_http_client(
    url: str,
    *,
    resolver: WindowsSystemProxyResolver | None = None,
    decision: ProxyDecision | None = None,
    **kwargs: object,
) -> httpx.Client:
    active_resolver = resolver or _default_system_proxy_resolver
    active_decision = decision or resolve_external_proxy(url, resolver=active_resolver)
    options = dict(kwargs)
    if active_resolver.is_windows:
        options["trust_env"] = False
        if active_decision.proxy_url:
            options["proxy"] = active_decision.proxy_url
    else:
        options.setdefault("trust_env", True)
    return httpx.Client(**options)


# 方法说明：创建忽略系统代理和代理环境变量的部署内网 HTTP 客户端。
def direct_http_client(**kwargs: object) -> httpx.Client:
    options = dict(kwargs)
    options["trust_env"] = False
    return httpx.Client(**options)


# 方法说明：读取当前 Windows 用户的手动代理、绕过、PAC 和 WPAD 配置。
def _read_windows_proxy_config() -> WindowsProxyConfig:
    if os.name != "nt":
        return WindowsProxyConfig()
    winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read_config = winhttp.WinHttpGetIEProxyConfigForCurrentUser
    read_config.argtypes = [ctypes.POINTER(_CurrentUserProxyConfig)]
    read_config.restype = wintypes.BOOL
    global_free = kernel32.GlobalFree
    global_free.argtypes = [ctypes.c_void_p]
    global_free.restype = ctypes.c_void_p

    raw = _CurrentUserProxyConfig()
    if not read_config(ctypes.byref(raw)):
        raise ctypes.WinError(ctypes.get_last_error())
    pointers = [raw.auto_config_url, raw.proxy, raw.bypass]
    try:
        return WindowsProxyConfig(
            auto_detect=bool(raw.auto_detect),
            auto_config_url=_read_wide_string(raw.auto_config_url),
            proxy=_read_wide_string(raw.proxy),
            bypass=_read_wide_string(raw.bypass),
        )
    finally:
        for pointer in pointers:
            if pointer:
                global_free(pointer)


# 方法说明：使用 WinHTTP 对 PAC 地址或 WPAD 规则进行单 URL 求值。
def _resolve_windows_auto_proxy(url: str, config: WindowsProxyConfig) -> str | None:
    if os.name != "nt":
        return None
    winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_session = winhttp.WinHttpOpen
    open_session.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    open_session.restype = ctypes.c_void_p
    close_handle = winhttp.WinHttpCloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = wintypes.BOOL
    get_proxy = winhttp.WinHttpGetProxyForUrl
    get_proxy.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_AutoProxyOptions),
        ctypes.POINTER(_ProxyInfo),
    ]
    get_proxy.restype = wintypes.BOOL
    global_free = kernel32.GlobalFree
    global_free.argtypes = [ctypes.c_void_p]
    global_free.restype = ctypes.c_void_p

    session = open_session(
        "ComicEnhancer/0.1",
        WINHTTP_ACCESS_TYPE_NO_PROXY,
        None,
        None,
        0,
    )
    if not session:
        raise ctypes.WinError(ctypes.get_last_error())
    options = _AutoProxyOptions()
    if config.auto_config_url:
        options.flags |= WINHTTP_AUTOPROXY_CONFIG_URL
        options.auto_config_url = config.auto_config_url
    elif config.auto_detect:
        options.flags |= WINHTTP_AUTOPROXY_AUTO_DETECT
        options.auto_detect_flags = (
            WINHTTP_AUTO_DETECT_TYPE_DHCP | WINHTTP_AUTO_DETECT_TYPE_DNS_A
        )
    options.auto_logon = True
    info = _ProxyInfo()
    try:
        if not get_proxy(session, url, ctypes.byref(options), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.access_type == WINHTTP_ACCESS_TYPE_NO_PROXY:
            return None
        if info.access_type != WINHTTP_ACCESS_TYPE_NAMED_PROXY:
            return None
        return _read_wide_string(info.proxy)
    finally:
        for pointer in (info.proxy, info.bypass):
            if pointer:
                global_free(pointer)
        close_handle(session)


# 方法说明：将 Windows API 分配的宽字符串指针转换为 Python 文本。
def _read_wide_string(pointer: int | None) -> str:
    return ctypes.wstring_at(pointer) if pointer else ""


# 方法说明：从 Windows 代理字符串中选择当前 URL 对应的首个 HTTP 代理。
def _select_proxy_url(proxy_spec: str, target_url: str) -> str | None:
    if not proxy_spec:
        return None
    target_scheme = urlparse(target_url).scheme.lower()
    scheme_entries: dict[str, list[str]] = {}
    general_entries: list[str] = []
    for raw_entry in proxy_spec.split(";"):
        entry = raw_entry.strip()
        if not entry or entry.upper() == "DIRECT":
            continue
        if "=" in entry:
            scheme, value = entry.split("=", 1)
            scheme_entries.setdefault(scheme.strip().lower(), []).append(value.strip())
        else:
            general_entries.append(entry)
    candidates = scheme_entries.get(target_scheme, general_entries)
    for candidate in candidates:
        normalized = _normalize_proxy_url(candidate)
        if normalized:
            return normalized
    return None


# 方法说明：将 WinHTTP 返回的代理端点规范化为 httpx 可接受的 URL。
def _normalize_proxy_url(value: str) -> str | None:
    candidate = value.strip()
    candidate = re.sub(r"^(?:PROXY|HTTP|HTTPS)\s+", "", candidate, flags=re.I)
    if not candidate or candidate.upper() == "DIRECT":
        return None
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    return candidate if parsed.scheme in {"http", "https", "socks5"} and parsed.hostname else None


# 方法说明：判断目标 URL 是否命中 Windows 手动代理绕过规则。
def _matches_bypass(url: str, bypass_spec: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if not host or not bypass_spec:
        return False
    host_with_port = f"{host}:{parsed.port}" if parsed.port else host
    for raw_pattern in re.split(r"[;,]", bypass_spec):
        pattern = raw_pattern.strip().casefold()
        if not pattern or pattern == "<-loopback>":
            continue
        if pattern == "<local>" and "." not in host:
            return True
        if "://" in pattern:
            pattern_url = urlparse(pattern)
            pattern = pattern_url.netloc or pattern_url.path
        if pattern.startswith(".") and host.endswith(pattern):
            return True
        if fnmatch.fnmatchcase(host, pattern) or fnmatch.fnmatchcase(
            host_with_port,
            pattern,
        ):
            return True
    return False


# 方法说明：每个目标主机和决策类型只记录一次代理路由摘要。
def _log_proxy_decision(url: str, decision: ProxyDecision, started: float) -> None:
    host = urlparse(url).hostname or ""
    key = (host, decision.source, decision.proxied)
    with _logged_proxy_decisions_lock:
        if key in _logged_proxy_decisions:
            return
        _logged_proxy_decisions.add(key)
    log_operation(
        logger,
        logging.INFO,
        feature="外部HTTP系统代理",
        parameters={"host": host},
        result={
            "status": "ready",
            "source": decision.source,
            "route": "proxy" if decision.proxied else "direct",
        },
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


_default_system_proxy_resolver = WindowsSystemProxyResolver()
_logged_proxy_decisions: set[tuple[str, str, bool]] = set()
_logged_proxy_decisions_lock = threading.Lock()
