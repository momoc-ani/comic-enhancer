from io import BytesIO
import json
import logging
from pathlib import Path

from PIL import Image

from comic_enhancer.character_library import (
    CharacterColorEvidence,
    CharacterLibraryBuilder,
    CharacterLibraryRepository,
    CharacterPageContext,
    CharacterPromptContext,
    CharacterProfile,
    CharacterReferenceAsset,
    PreparedCharacter,
)
from comic_enhancer.character_vision import (
    CharacterPageAnalysis,
    CharacterProfileAnalysis,
    PageCharacterInstance,
    PageCharacterMatch,
    ProfileRegion,
)
from comic_enhancer.inference import InferenceAssets
from comic_enhancer.inference.comfyui import ComfyUIBackend, PresetWorkflowLoader
from comic_enhancer.inference.comfyui.strategies import (
    FLUX2_CHARACTER_PROCESSING_REVISION,
    Flux2CharacterModeStrategy,
)
from comic_enhancer.models import ProcessOptions


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 方法说明：生成测试图片字节。
def png_bytes(color="white", size=(16, 24)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class FakeAnalyzer:
    """为角色库测试提供确定性的视觉分析结果。"""

    def __init__(self):
        self.profile_calls = 0
        self.page_calls = 0

    @property
    def model_revision(self):
        """返回测试模型版本。"""
        return "fake-qwen-v1"

    # 方法说明：始终报告测试 sidecar 可用。
    def ready(self):
        return True

    # 方法说明：返回覆盖整张纯色参考图的发色区域。
    def analyze_profile(self, *, character_id, display_name, image_bytes):
        self.profile_calls += 1
        return CharacterProfileAnalysis(
            character_id=character_id,
            stable_traits=["long straight hair"],
            outfit_traits=["high collar jacket"],
            regions=[
                ProfileRegion(
                    part="hair",
                    box_2d=(0, 0, 1000, 1000),
                    confidence=0.98,
                    structural_trait="long straight hair",
                )
            ],
        )

    # 方法说明：把首个候选稳定匹配到一个页面矩形。
    def analyze_page(self, *, image_bytes, candidates):
        self.page_calls += 1
        candidate = candidates[0]
        return CharacterPageAnalysis(
            characters=[
                PageCharacterMatch(
                    character_id=candidate["character_id"],
                    reference_slot=candidate["reference_slot"],
                    visible=True,
                    outfit_matches_reference=True,
                    instances=[
                        PageCharacterInstance(
                            panel_id=1,
                            box_2d=(100, 100, 700, 900),
                            confidence=0.96,
                            match_evidence=["hair shape"],
                        )
                    ],
                )
            ]
        )


# 方法说明：验证角色档案、本地颜色采样和页面计划会持久缓存。
def test_character_library_builds_pixel_colors_and_reuses_cached_analysis(
    tmp_path,
    caplog,
):
    analyzer = FakeAnalyzer()
    builder = CharacterLibraryBuilder(
        repository=CharacterLibraryRepository(tmp_path / "library"),
        analyzer=analyzer,
        min_confidence=0.75,
    )
    reference = CharacterReferenceAsset(
        character_id="work:character-a",
        display_name="角色 A",
        image_bytes=png_bytes((210, 30, 50)),
    )
    source = png_bytes("white")

    with caplog.at_level(logging.INFO):
        first = builder.prepare(
            work_key="copy_manga:123",
            image_bytes=source,
            references=(reference,),
        )
        second = builder.prepare(
            work_key="copy_manga:123",
            image_bytes=source,
            references=(reference,),
        )

    assert analyzer.profile_calls == 1
    assert analyzer.page_calls == 1
    assert first.digest == second.digest
    assert first.characters[0].profile.colors[0].part == "hair"
    red, green, blue = first.characters[0].profile.colors[0].rgb
    assert red > 180 and green < 60 and blue < 80
    assert (tmp_path / "library" / "character-library.sqlite3").is_file()
    assert list((tmp_path / "library" / "images").rglob("*.png"))
    messages = [record.getMessage() for record in caplog.records]
    assert any("功能=角色档案构建 参数=" in message for message in messages)
    assert any("功能=角色页面计划准备 参数=" in message for message in messages)
    assert any("耗时_ms=" in message for message in messages)


# 方法说明：验证静态角色提示上下文只分析参考图，不对漫画页调用 VLM。
def test_static_character_prompt_context_does_not_analyze_page(tmp_path):
    analyzer = FakeAnalyzer()
    builder = CharacterLibraryBuilder(
        repository=CharacterLibraryRepository(tmp_path / "library"),
        analyzer=analyzer,
    )
    reference = CharacterReferenceAsset(
        character_id="work:character-a",
        display_name="角色 A",
        image_bytes=png_bytes((210, 30, 50)),
    )

    context = builder.prepare_prompt_context(
        work_key="copy_manga:123",
        references=(reference,),
    )

    assert isinstance(context, CharacterPromptContext)
    assert len(context.characters) == 1
    assert analyzer.profile_calls == 1
    assert analyzer.page_calls == 0


class StubCharacterLibrary:
    """为 ComfyUI 策略测试返回预制静态角色提示上下文。"""

    def __init__(self, prompt_context):
        self.prompt_context = prompt_context
        self.prepare_calls = 0

    @property
    def model_revision(self):
        """返回测试角色库模型版本。"""
        return "stub-qwen-v1"

    # 方法说明：报告角色库 sidecar 已就绪。
    def ready(self):
        return True

    # 方法说明：返回预制静态角色提示上下文，不读取漫画页面。
    def prepare_prompt_context(self, **_kwargs):
        self.prepare_calls += 1
        return self.prompt_context


# 方法说明：构造角色策略测试使用的完整页面上下文。
def character_context(reference):
    profile = CharacterProfile(
        work_key="copy_manga:123",
        character_id=reference.character_id,
        display_name=reference.display_name,
        reference_sha256=reference.sha256,
        stable_traits=["long straight hair"],
        outfit_traits=["high collar jacket"],
        colors=[
            CharacterColorEvidence(
                part="hair",
                rgb=(210, 30, 50),
                confidence=0.98,
            )
        ],
    )
    analysis = CharacterPageAnalysis(
        characters=[
            PageCharacterMatch(
                character_id=reference.character_id,
                reference_slot=1,
                visible=True,
                outfit_matches_reference=True,
                instances=[
                    PageCharacterInstance(
                        panel_id=2,
                        box_2d=(100, 100, 700, 900),
                        confidence=0.96,
                    )
                ],
            )
        ]
    )
    return CharacterPageContext(
        characters=(PreparedCharacter(1, reference, profile),),
        page_analysis=analysis,
        digest="context-digest-v1",
    )


# 方法说明：构造包含完整角色部件颜色的静态提示上下文。
def character_prompt_context(reference):
    profile = CharacterProfile(
        work_key="copy_manga:123",
        character_id=reference.character_id,
        display_name=reference.display_name,
        reference_sha256=reference.sha256,
        stable_traits=["long straight hair"],
        outfit_traits=["layered school uniform", "stockings and ankle boots"],
        colors=[
            CharacterColorEvidence(part="hair", rgb=(20, 30, 40), confidence=0.98),
            CharacterColorEvidence(part="left_eye", rgb=(40, 120, 200), confidence=0.98),
            CharacterColorEvidence(part="right_eye", rgb=(40, 120, 200), confidence=0.98),
            CharacterColorEvidence(part="eyebrow", rgb=(20, 30, 40), confidence=0.90),
            CharacterColorEvidence(part="mouth", rgb=(180, 60, 80), confidence=0.90),
            CharacterColorEvidence(part="face_marking", rgb=(180, 20, 30), confidence=0.90),
            CharacterColorEvidence(part="skin", rgb=(235, 190, 160), confidence=0.98),
            CharacterColorEvidence(part="upper_clothing", rgb=(30, 60, 120), confidence=0.98),
            CharacterColorEvidence(part="lower_clothing", rgb=(40, 45, 55), confidence=0.98),
            CharacterColorEvidence(part="inner_clothing", rgb=(240, 240, 230), confidence=0.90),
            CharacterColorEvidence(part="outer_clothing", rgb=(70, 80, 95), confidence=0.90),
            CharacterColorEvidence(part="headwear", rgb=(80, 20, 100), confidence=0.90),
            CharacterColorEvidence(part="hair_accessory", rgb=(220, 40, 80), confidence=0.90),
            CharacterColorEvidence(part="neckwear", rgb=(220, 40, 40), confidence=0.90),
            CharacterColorEvidence(part="gloves", rgb=(25, 25, 25), confidence=0.90),
            CharacterColorEvidence(part="belt", rgb=(120, 80, 40), confidence=0.90),
            CharacterColorEvidence(part="legwear", rgb=(35, 35, 45), confidence=0.98),
            CharacterColorEvidence(part="footwear", rgb=(80, 45, 25), confidence=0.98),
            CharacterColorEvidence(part="jewelry", rgb=(220, 180, 40), confidence=0.90),
            CharacterColorEvidence(part="accessory", rgb=(20, 150, 100), confidence=0.90),
            CharacterColorEvidence(part="prop", rgb=(150, 80, 30), confidence=0.90),
        ],
    )
    return CharacterPromptContext(
        characters=(PreparedCharacter(1, reference, profile),),
        digest="static-context-digest-v1",
    )


# 方法说明：验证角色档位绑定原图、三张角色参考图与静态调色提示，并执行结构保护。
def test_flux2_character_strategy_binds_static_palette_and_protects_structure(
    tmp_path,
    monkeypatch,
):
    workflow = PROJECT_ROOT / "workflows" / "flux2-klein-4b-qwen3-vl-character-colorize.json"
    reference = CharacterReferenceAsset(
        character_id="work:character-a",
        display_name="角色 A",
        image_bytes=png_bytes((210, 30, 50), (8, 12)),
    )
    library = StubCharacterLibrary(character_prompt_context(reference))
    loader = PresetWorkflowLoader(
        fast_workflow=PROJECT_ROOT / "workflows" / "sd15-colorize-fast.json",
        quality_workflow=PROJECT_ROOT / "workflows" / "sd15-colorize-quality.json",
        flux2_character_workflow=workflow,
    )
    backend = ComfyUIBackend(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
        workflow_loader=loader,
        flux2_character_enabled=True,
        flux2_character_workflow=workflow,
        character_library=library,
    )
    strategy = backend.mode_strategy("flux2_character")
    assert isinstance(strategy, Flux2CharacterModeStrategy)
    monkeypatch.setattr(strategy, "available", lambda: True)
    captured = {}

    # 方法说明：模拟 ComfyUI 运行并执行真实提示词绑定回调。
    def run_character(
        workflow_template,
        *,
        input_images,
        output_prefix,
        prepare_workflow,
    ):
        workflow_copy = json.loads(json.dumps(workflow_template))
        prepare_workflow(workflow_copy)
        captured["workflow"] = workflow_copy
        captured["inputs"] = input_images
        return Image.new("RGB", (16, 24), (80, 120, 200))

    monkeypatch.setattr(backend.transport, "run", run_character)
    source_image = Image.new("RGB", (8, 12), "white")
    for y in range(source_image.height):
        source_image.putpixel((4, y), (0, 0, 0))
    source_stream = BytesIO()
    source_image.save(source_stream, format="PNG")
    source = source_stream.getvalue()
    assets = InferenceAssets(
        image_bytes=source,
        work_key="copy_manga:123",
        character_reference_assets=(reference,),
    )
    options = ProcessOptions(mode="flux2_character")
    revision = backend.cache_revision(options, assets)
    output_path = tmp_path / "character.webp"

    outcome = backend.process(assets, output_path, options)

    assert FLUX2_CHARACTER_PROCESSING_REVISION in revision
    assert "static-context-digest-v1" in revision
    assert library.prepare_calls == 1
    assert outcome.model_profile == "flux2-klein-4b-qwen3-vl-character"
    assert outcome.reference_applied is True
    assert outcome.processed_panels == 0
    assert captured["inputs"] == {
        "INPUT_IMAGE": source,
        "REFERENCE_IMAGE_1": reference.image_bytes,
        "REFERENCE_IMAGE_2": reference.image_bytes,
        "REFERENCE_IMAGE_3": reference.image_bytes,
    }
    prompt_nodes = {
        node.get("_meta", {}).get("title"): node
        for node in captured["workflow"].values()
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode"
    }
    prompt = prompt_nodes["Colorization Instruction"]["inputs"]["text"]
    for token in (
        "hair RGB(20, 30, 40)",
        "left eye RGB(40, 120, 200)",
        "face marking RGB(180, 20, 30)",
        "inner clothing RGB(240, 240, 230)",
        "stockings, socks, legwear or leg armor RGB(35, 35, 45)",
        "shoes, boots or foot armor RGB(80, 45, 25)",
        "jewelry or metal ornament RGB(220, 180, 40)",
        "existing character-held prop RGB(150, 80, 30)",
    ):
        assert token in prompt
    assert "must never add, remove, replace, reshape" in prompt
    assert "change chroma only" in prompt
    assert "must not leave backgrounds" in prompt
    assert "Fully color every existing non-text source region" in prompt
    assert "leave such source pixels unchanged" in prompt
    assert not any(
        node.get("class_type") == "ConditioningSetMask"
        for node in captured["workflow"].values()
        if isinstance(node, dict)
    )
    with Image.open(output_path) as output:
        assert output.size == (16, 24)
        assert max(output.getpixel((8, 12))) <= 40
        red, green, blue = output.getpixel((6, 12))
        assert blue >= red + 10
        assert blue >= green


# 方法说明：验证角色工作流使用三张参考图、空 latent 和完整四步采样。
def test_shipped_character_workflow_uses_three_references_and_empty_latent():
    path = PROJECT_ROOT / "workflows" / "flux2-klein-4b-qwen3-vl-character-colorize.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(workflow)
    titles = {
        node.get("_meta", {}).get("title")
        for node in workflow.values()
        if isinstance(node, dict)
    }

    assert "${" not in serialized
    for slot in range(1, 4):
        assert f"REFERENCE_IMAGE_{slot}" in titles
    assert "Colorization Instruction" in titles
    assert not any(
        node.get("class_type") == "ConditioningSetMask"
        for node in workflow.values()
        if isinstance(node, dict)
    )
    assert sum(node.get("class_type") == "ReferenceLatent" for node in workflow.values()) == 8
    assert sum(node.get("class_type") == "EmptyFlux2LatentImage" for node in workflow.values()) == 1
    assert sum(node.get("class_type") == "SplitSigmas" for node in workflow.values()) == 0
    assert sum(node.get("class_type") == "Flux2Scheduler" for node in workflow.values()) == 1
    assert sum(node.get("class_type") == "SaveImage" for node in workflow.values()) == 1
    sampler = next(
        node for node in workflow.values() if node.get("class_type") == "SamplerCustomAdvanced"
    )
    scheduler_id = next(
        node_id for node_id, node in workflow.items() if node.get("class_type") == "Flux2Scheduler"
    )
    empty_latent_id = next(
        node_id
        for node_id, node in workflow.items()
        if node.get("class_type") == "EmptyFlux2LatentImage"
    )
    assert sampler["inputs"]["latent_image"] == [empty_latent_id, 0]
    assert sampler["inputs"]["sigmas"] == [scheduler_id, 0]
    assert workflow["34"]["inputs"]["filename_prefix"] == "comic-enhancer/flux2-character"


# 方法说明：验证低置信度和重叠视图产生的同角色重复框会被过滤。
def test_character_library_rejects_low_confidence_and_overlapping_duplicates(
    tmp_path,
):
    reference = CharacterReferenceAsset(
        character_id="work:character-a",
        display_name="角色 A",
        image_bytes=png_bytes((210, 30, 50)),
    )
    profile = CharacterProfile(
        work_key="copy_manga:123",
        character_id=reference.character_id,
        display_name=reference.display_name,
        reference_sha256=reference.sha256,
    )
    prepared = [PreparedCharacter(1, reference, profile)]
    builder = CharacterLibraryBuilder(
        repository=CharacterLibraryRepository(tmp_path / "library"),
        analyzer=FakeAnalyzer(),
        min_confidence=0.75,
    )
    analysis = CharacterPageAnalysis(
        characters=[
            PageCharacterMatch(
                character_id=reference.character_id,
                reference_slot=1,
                visible=True,
                outfit_matches_reference=True,
                instances=[
                    PageCharacterInstance(
                        panel_id=1,
                        box_2d=(375, 400, 999, 616),
                        confidence=0.95,
                    ),
                    PageCharacterInstance(
                        panel_id=51,
                        box_2d=(500, 300, 900, 540),
                        confidence=0.90,
                    ),
                    PageCharacterInstance(
                        panel_id=2,
                        box_2d=(50, 650, 250, 900),
                        confidence=0.80,
                    ),
                ],
            )
        ]
    )

    validated = builder._validate_page_analysis(analysis, prepared)

    assert validated.characters[0].visible is True
    assert [
        instance.box_2d for instance in validated.characters[0].instances
    ] == [(375, 400, 999, 616)]
