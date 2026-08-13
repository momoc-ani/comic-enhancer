from comic_enhancer.analysis import PageAnalysisStore
from comic_enhancer.models import PageAnalysis


def test_page_analysis_store_round_trip_uses_image_hash(tmp_path):
    store = PageAnalysisStore(tmp_path)
    image_bytes = b"page"
    analysis = PageAnalysis(
        image_hash=store.image_hash(image_bytes),
        width=100,
        height=200,
        analyzer_profile="magiv2@test",
    )

    store.put(analysis)

    assert store.get(image_bytes) == analysis
    assert store.get(b"other") is None


def test_page_analysis_store_is_scoped_to_work(tmp_path):
    store = PageAnalysisStore(tmp_path)
    image_bytes = b"same-page"
    analysis = PageAnalysis(
        image_hash=store.image_hash(image_bytes),
        width=100,
        height=200,
        analyzer_profile="magiv2@test",
    )

    store.put(analysis, work_key="copy_manga:work-a")

    assert store.get(image_bytes, work_key="copy_manga:work-a") == analysis
    assert store.get(image_bytes, work_key="copy_manga:work-b") is None
