from io import BytesIO

import httpx
import pytest
from PIL import Image

from comic_enhancer.networking import ProxyDecision

from comic_enhancer.references import (
    ReferenceImageStore,
    assess_reference_image,
    reference_quality_rank,
)


# 方法说明：验证参考图规范化会限制最大尺寸。
def test_normalize_reference_limits_size():
    source = BytesIO()
    Image.new("RGB", (3000, 1500), "red").save(source, format="JPEG")

    normalized = ReferenceImageStore._normalize(source.getvalue())

    with Image.open(BytesIO(normalized)) as image:
        assert image.size == (2048, 1024)
        assert image.mode == "RGB"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/cover.png",
        "http://localhost/cover.png",
    ],
)
# 方法说明：验证参考图地址会拒绝非公网目标。
def test_reference_url_rejects_non_public_targets(url):
    with pytest.raises(ValueError):
        ReferenceImageStore._validate_public_url(url)


# 方法说明：验证代理请求不会被本机混合 DNS 中的保留地址误判。
def test_proxied_reference_skips_local_dns_validation(monkeypatch):
    monkeypatch.setattr(
        "comic_enhancer.references.store.socket.getaddrinfo",
        lambda *_args: (_ for _ in ()).throw(AssertionError("代理请求不应查询本机 DNS")),
    )

    ReferenceImageStore._validate_public_url(
        "https://lain.bgm.tv/pic/crt/l/example.jpg",
        proxied=True,
    )


# 方法说明：验证代理请求仍会拒绝私网 IP 字面量。
def test_proxied_reference_rejects_private_ip_literal():
    with pytest.raises(ValueError, match="non-public"):
        ReferenceImageStore._validate_public_url(
            "http://127.0.0.1/reference.png",
            proxied=True,
        )


# 方法说明：验证直连请求仍会拒绝解析到私网的域名。
def test_direct_reference_rejects_private_dns_result(monkeypatch):
    monkeypatch.setattr(
        "comic_enhancer.references.store.socket.getaddrinfo",
        lambda *_args: [(2, 1, 6, "", ("10.0.0.8", 443))],
    )

    with pytest.raises(ValueError, match="non-public"):
        ReferenceImageStore._validate_public_url(
            "https://private.example/reference.png",
            proxied=False,
        )


# 方法说明：验证每次重定向都会重新解析代理并拒绝私网目标。
def test_reference_redirect_revalidates_private_target(monkeypatch, tmp_path):
    decisions = []
    requested = []

    # 方法说明：记录每个重定向目标的代理决策。
    def resolve_proxy(url):
        decisions.append(url)
        return ProxyDecision(source="manual", proxy_url="http://proxy.example:8080")

    # 方法说明：为首个公网请求返回指向私网地址的重定向。
    def create_client(url, **_kwargs):
        requested.append(url)

        # 方法说明：返回当前测试请求对应的重定向响应。
        def handler(request):
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/private.png"},
                request=request,
            )

        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "comic_enhancer.references.store.resolve_external_proxy",
        resolve_proxy,
    )
    monkeypatch.setattr(
        "comic_enhancer.references.store.external_http_client",
        create_client,
    )

    store = ReferenceImageStore(tmp_path)
    with pytest.raises(ValueError, match="non-public"):
        store._download("https://public.example/reference.png")

    assert decisions == [
        "https://public.example/reference.png",
        "http://127.0.0.1/private.png",
    ]
    assert requested == ["https://public.example/reference.png"]


# 方法说明：验证彩色参考图的质量排名高于灰度图。
def test_reference_quality_prefers_color_before_resolution():
    gray = BytesIO()
    Image.new("L", (1000, 1400), 128).save(gray, format="PNG")
    color = BytesIO()
    Image.new("RGB", (320, 480), (220, 40, 80)).save(color, format="PNG")

    gray_quality = assess_reference_image(gray.getvalue())
    color_quality = assess_reference_image(color.getvalue())

    assert reference_quality_rank(
        color_quality,
        confirmed_source=True,
        provider="anilist",
    ) > reference_quality_rank(
        gray_quality,
        confirmed_source=True,
        provider="bangumi",
    )


# 方法说明：验证同为彩色时优先选择更大分辨率参考图。
def test_reference_quality_prefers_larger_color_image():
    small = BytesIO()
    Image.new("RGB", (230, 345), (140, 80, 110)).save(small, format="PNG")
    large = BytesIO()
    Image.new("RGB", (690, 1050), (140, 80, 110)).save(large, format="PNG")

    small_quality = assess_reference_image(small.getvalue())
    large_quality = assess_reference_image(large.getvalue())

    assert reference_quality_rank(
        large_quality,
        confirmed_source=True,
        provider="bangumi",
    ) > reference_quality_rank(
        small_quality,
        confirmed_source=True,
        provider="anilist",
    )


# 方法说明：验证完整人物构图优先于鲜艳但裁切的肖像。
def test_reference_quality_prefers_full_body_composition_over_vivid_portrait():
    portrait = Image.new("RGB", (230, 345), (220, 40, 80))
    portrait_source = BytesIO()
    portrait.save(portrait_source, format="PNG")

    full_body = Image.new("RGB", (690, 1050), "white")
    for y in range(60, 1020):
        half_width = 75 if y < 300 else 145
        for x in range(345 - half_width, 345 + half_width):
            full_body.putpixel((x, y), (80, 90, 130))
    full_body_source = BytesIO()
    full_body.save(full_body_source, format="PNG")

    portrait_quality = assess_reference_image(portrait_source.getvalue())
    full_body_quality = assess_reference_image(full_body_source.getvalue())

    assert portrait_quality.full_body is False
    assert full_body_quality.full_body is True
    assert reference_quality_rank(
        full_body_quality,
        confirmed_source=True,
        provider="bangumi",
    ) > reference_quality_rank(
        portrait_quality,
        confirmed_source=True,
        provider="anilist",
    )
