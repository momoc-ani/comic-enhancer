from comic_enhancer.inference.comfyui.text_regions import OCRTextRegionDetector


# 方法说明：验证检测器只运行文字检测，并按原图内容哈希复用检测框。
def test_ocr_text_region_detector_reuses_cached_detection():
    calls = []

    # 方法说明：模拟 RapidOCR 引擎并记录调用参数。
    def fake_engine(image_bytes, *, use_det, use_cls, use_rec):
        calls.append((image_bytes, use_det, use_cls, use_rec))
        return (
            [
                [[1, 2], [5, 2], [5, 8], [1, 8]],
                [[1, 2]],
            ],
            [0.01],
        )

    # 方法说明：返回测试使用的内存 OCR 引擎。
    def create_engine():
        return fake_engine

    detector = OCRTextRegionDetector(
        cache_size=2,
        engine_factory=create_engine,
    )

    first = detector.detect(b"same-image")
    second = detector.detect(b"same-image")

    assert first.regions == (((1.0, 2.0), (5.0, 2.0), (5.0, 8.0), (1.0, 8.0)),)
    assert first.cache_hit is False
    assert first.initialized_now is True
    assert second.regions == first.regions
    assert second.cache_hit is True
    assert second.initialized_now is False
    assert calls == [(b"same-image", True, False, False)]
