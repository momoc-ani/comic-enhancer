import logging

from comic_enhancer.character_vision.llamacpp import (
    LlamaCppCharacterVisionAnalyzer,
)
from comic_enhancer.inference.comfyui.transport import ComfyUITransport
from comic_enhancer.networking import (
    WindowsProxyConfig,
    WindowsSystemProxyResolver,
    direct_http_client,
    external_http_client,
)


class FakeResponse:
    """提供网络路由测试使用的最小 HTTP 响应。"""

    # 方法说明：初始化状态码和 JSON 数据。
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    # 方法说明：模拟成功响应校验。
    def raise_for_status(self):
        return None

    # 方法说明：返回测试配置的 JSON 数据。
    def json(self):
        return self.payload


class FakeClient:
    """记录 HTTP 客户端参数并返回预设响应。"""

    # 方法说明：保存客户端参数和请求处理函数。
    def __init__(self, captured, handler, **kwargs):
        captured.append(kwargs)
        self.handler = handler

    # 方法说明：进入测试客户端上下文。
    def __enter__(self):
        return self

    # 方法说明：退出测试客户端上下文。
    def __exit__(self, *_args):
        return False

    # 方法说明：返回当前 URL 对应的模拟响应。
    def get(self, url, **_kwargs):
        return self.handler(str(url))


# 方法说明：创建使用固定 Windows 配置的系统代理解析器。
def resolver(config, *, auto_proxy_resolver=None):
    return WindowsSystemProxyResolver(
        platform_name="nt",
        config_reader=lambda: config,
        auto_proxy_resolver=auto_proxy_resolver,
    )


# 方法说明：验证 Windows 手动代理按协议选择并遵守绕过规则。
def test_windows_manual_proxy_respects_scheme_and_bypass():
    system = resolver(
        WindowsProxyConfig(
            proxy="http=127.0.0.1:7890;https=127.0.0.1:7891",
            bypass="<local>;*.internal.example",
        )
    )

    assert system.resolve("http://api.example.com/data").proxy_url == (
        "http://127.0.0.1:7890"
    )
    assert system.resolve("https://api.example.com/data").proxy_url == (
        "http://127.0.0.1:7891"
    )
    assert system.resolve("https://metadata.internal.example/data").source == (
        "manual_bypass"
    )
    assert system.resolve("http://intranet/data").source == "manual_bypass"


# 方法说明：验证系统代理关闭时忽略代理环境变量并直接连接。
def test_windows_disabled_proxy_ignores_environment(monkeypatch):
    captured = []
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-proxy:8888")
    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient(
            captured,
            lambda _url: FakeResponse(),
            **kwargs,
        ),
    )

    external_http_client(
        "https://api.example.com",
        resolver=resolver(WindowsProxyConfig()),
        timeout=5,
    )

    assert captured == [{"timeout": 5, "trust_env": False}]


# 方法说明：验证 Windows 手动代理会传入 httpx 且不会读取环境代理。
def test_windows_enabled_proxy_is_passed_to_httpx(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient(
            captured,
            lambda _url: FakeResponse(),
            **kwargs,
        ),
    )

    external_http_client(
        "https://api.example.com",
        resolver=resolver(WindowsProxyConfig(proxy="127.0.0.1:7890")),
    )

    assert captured == [
        {"trust_env": False, "proxy": "http://127.0.0.1:7890"}
    ]


# 方法说明：验证代理地址和认证信息不会进入系统代理决策日志。
def test_windows_proxy_log_hides_proxy_endpoint(monkeypatch, caplog):
    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient([], lambda _url: FakeResponse(), **kwargs),
    )
    system = resolver(
        WindowsProxyConfig(proxy="http://proxy-user:proxy-secret@127.0.0.1:7890")
    )

    with caplog.at_level(logging.INFO, logger="comic_enhancer.networking"):
        external_http_client(
            "https://proxy-log.example/data",
            resolver=system,
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "功能=外部HTTP系统代理" in messages
    assert '"host":"proxy-log.example"' in messages
    assert '"route":"proxy"' in messages
    assert "proxy-user" not in messages
    assert "proxy-secret" not in messages
    assert "127.0.0.1:7890" not in messages


# 方法说明：验证 PAC/WPAD 结果按目标 URL 转换为代理或直连决策。
def test_windows_pac_and_wpad_are_evaluated_per_url():
    calls = []

    # 方法说明：返回测试 PAC 对不同目标的路由结果。
    def evaluate(url, config):
        calls.append((url, config.auto_config_url, config.auto_detect))
        return None if "direct" in url else "PROXY pac-proxy:8080; DIRECT"

    pac = resolver(
        WindowsProxyConfig(auto_config_url="http://config/proxy.pac"),
        auto_proxy_resolver=evaluate,
    )
    wpad = resolver(
        WindowsProxyConfig(auto_detect=True),
        auto_proxy_resolver=evaluate,
    )

    assert pac.resolve("https://proxy.example/data").proxy_url == (
        "http://pac-proxy:8080"
    )
    assert pac.resolve("https://direct.example/data").proxy_url is None
    assert wpad.resolve("https://proxy.example/data").source == "wpad"
    assert calls == [
        ("https://proxy.example/data", "http://config/proxy.pac", False),
        ("https://direct.example/data", "http://config/proxy.pac", False),
        ("https://proxy.example/data", "", True),
    ]


# 方法说明：验证 PAC 失败时才回退 WPAD，PAC 的 DIRECT 结果不会继续回退。
def test_windows_pac_failure_falls_back_to_wpad_only():
    calls = []

    # 方法说明：模拟 PAC 失败后由 WPAD 返回代理。
    def evaluate(url, config):
        calls.append((url, bool(config.auto_config_url), config.auto_detect))
        if config.auto_config_url:
            raise OSError("pac unavailable")
        return "wpad-proxy:8080"

    system = resolver(
        WindowsProxyConfig(
            auto_config_url="http://config/proxy.pac",
            auto_detect=True,
        ),
        auto_proxy_resolver=evaluate,
    )

    decision = system.resolve("https://api.example.com")

    assert decision.source == "wpad"
    assert decision.proxy_url == "http://wpad-proxy:8080"
    assert calls == [
        ("https://api.example.com", True, False),
        ("https://api.example.com", False, True),
    ]


# 方法说明：验证非 Windows 平台继续使用 httpx 环境代理行为。
def test_non_windows_external_client_keeps_environment_behavior(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient(
            captured,
            lambda _url: FakeResponse(),
            **kwargs,
        ),
    )
    system = WindowsSystemProxyResolver(platform_name="posix")

    external_http_client("https://api.example.com", resolver=system)

    assert captured == [{"trust_env": True}]


# 方法说明：验证部署内网客户端始终禁用系统代理和环境代理。
def test_direct_http_client_always_disables_proxy_environment(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient(
            captured,
            lambda _url: FakeResponse(),
            **kwargs,
        ),
    )

    direct_http_client(timeout=3)

    assert captured == [{"timeout": 3, "trust_env": False}]


# 方法说明：验证 ComfyUI 健康检查固定使用直连客户端。
def test_comfyui_health_check_never_uses_proxy(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient(
            captured,
            lambda _url: FakeResponse(status_code=200),
            **kwargs,
        ),
    )
    transport = ComfyUITransport(
        base_url="http://192.168.38.226:8192",
        timeout_seconds=30,
        poll_interval_seconds=0.1,
    )

    assert transport.ready() is True
    assert captured == [{"timeout": 2, "trust_env": False}]


# 方法说明：验证 Qwen3-VL 健康检查固定使用直连客户端。
def test_qwen_sidecar_health_check_never_uses_proxy(monkeypatch):
    captured = []

    # 方法说明：为健康检查和模型列表返回不同响应。
    def handler(url):
        if url.endswith("/v1/models"):
            return FakeResponse(payload={"data": [{"id": "qwen3-vl-4b"}]})
        return FakeResponse()

    monkeypatch.setattr(
        "comic_enhancer.networking.httpx.Client",
        lambda **kwargs: FakeClient(captured, handler, **kwargs),
    )
    analyzer = LlamaCppCharacterVisionAnalyzer(
        base_url="http://127.0.0.1:8080",
        api_key="",
        model_id="qwen3-vl-4b",
        deployment_revision="test",
        timeout_seconds=30,
    )

    assert analyzer.ready() is True
    assert captured == [
        {
            "headers": {},
            "timeout": 3,
            "trust_env": False,
        }
    ]
