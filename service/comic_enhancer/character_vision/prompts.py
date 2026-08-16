from __future__ import annotations

import json


PROFILE_TEMPLATE_REVISION = "qwen-profile-regions-v3-complete-character-palette"
PAGE_TEMPLATE_REVISION = "qwen-page-character-grounding-v5-tight-regions"
PROMPT_PLANNER_REVISION = "flux2-character-prompts-v5-pixel-lock"


# 方法说明：构造只提取角色稳定特征和采色区域的提示词。
def build_profile_prompt(character_id: str, display_name: str) -> str:
    mapping = json.dumps(
        {"character_id": character_id, "display_name": display_name},
        ensure_ascii=False,
    )
    return (
        "任务仅限分析 Picture 1 中已确认角色的外观结构和可采色区域，不要描述剧情、背景、"
        "性格或姿势。角色映射为 "
        f"{mapping}。只返回 JSON 对象，字段必须恰好为 character_id、stable_traits、"
        "outfit_traits、regions。character_id 必须原样返回。stable_traits 只写发型轮廓、"
        "固定配饰、眼睛形状等跨服装稳定的短特征；outfit_traits 只写参考图可见的衣服结构和"
        "配饰结构，不写任何颜色名称。regions 中每项字段必须恰好为 part、box_2d、confidence、"
        "structural_trait；part 只能是 hair、left_eye、right_eye、eyebrow、mouth、face_marking、"
        "skin、upper_clothing、lower_clothing、inner_clothing、outer_clothing、headwear、"
        "hair_accessory、neckwear、gloves、belt、legwear、footwear、jewelry、accessory、prop。"
        "legwear 表示袜子、丝袜、裤袜、护腿或腿甲；footwear 表示鞋、靴子或足部装甲；"
        "face_marking 表示原图已有的纹身、胎记或脸部涂装；prop 仅表示角色明确持有且具有稳定颜色的物件。"
        "box_2d 使用 0 到 1000 的 [x1,y1,x2,y2]，框应位于对应"
        "部件内部并尽量避开背景、墨线、阴影和高光。stable_traits 和 outfit_traits 的每一项"
        "都必须是纯字符串，禁止在这两个数组中放对象。示例结构："
        '{"character_id":"原样ID","stable_traits":["long straight hair"],'
        '"outfit_traits":["high collar jacket"],"regions":[{"part":"hair",'
        '"box_2d":[100,100,500,500],"confidence":0.9,'
        '"structural_trait":"long straight hair"}]}。无法确认的部件不要输出，禁止猜测颜色。'
    )


# 方法说明：构造候选角色身份匹配与定位提示词。
def build_page_prompt(candidates: list[dict[str, object]]) -> str:
    mapping = [
        {
            "character_id": item["character_id"],
            "display_name": item["display_name"],
            "reference_slot": item["reference_slot"],
            "stable_traits": item.get("stable_traits", []),
            "outfit_traits": item.get("outfit_traits", []),
        }
        for item in candidates
    ]
    return (
        "任务仅限角色身份匹配、当前服装结构比对和人物定位，不要总结场景、剧情、对白或颜色。"
        "Picture 1 是当前灰度漫画页或保持原宽的连续页面分段，后续图片按 reference_slot 对应候选角色。逐个人物比较"
        "发型轮廓、脸部特征、固定配饰和服装结构；不得依赖灰度页中的颜色，不得为了提高覆盖率"
        "强行匹配。候选映射为 "
        f"{json.dumps(mapping, ensure_ascii=False)}。只返回 JSON 对象，字段必须恰好为 characters、"
        "unmatched_people。characters 必须为每个候选各返回一项，字段恰好为 character_id、"
        "reference_slot、visible、outfit_matches_reference、instances。每个 instance 字段恰好为"
        "panel_id、box_2d、confidence、match_evidence、counter_evidence。box_2d 使用 Picture 1"
        "的 0 到 1000 坐标并紧贴人物在该分格中的完整可见轮廓：从最上方头发或帽子到最下方"
        "可见鞋、衣摆或被分格裁切的位置，并包含左右最外侧可见肢体和服装。矩形四边与可见"
        "轮廓之间的留白应尽量小于该边长度的 5%，不能包含对白框、文字、分格边界、其他人物"
        "或大块背景；人物被遮挡时只框实际可见部分。禁止只框脸部、跨分格或把同一实例重复"
        "返回。证据不足时"
        "visible=false 且 instances=[]。outfit_matches_reference 只有在漫画页衣服结构与参考图"
        "明确相符时才为 true。unmatched_people 每项字段恰好为 panel_id、box_2d、reason，"
        "不得猜测候选列表外的姓名。必须逐分格检查整页；同一角色在不同分格重复出现时，每个"
        "全身、半身、近景或脸部实例都要单独返回，禁止只返回首次出现或最大的人物。panel_id "
        "从 1 开始，按从上到下、从左到右编号。"
    )


# 方法说明：生成覆盖整页背景和结构保护的固定 FLUX.2 提示词。
def build_global_prompt() -> str:
    return (
        "Perform a pixel-preserving color overlay on this manga page without redrawing. "
        "Use natural coherent anime colors for existing characters, objects, and backgrounds. "
        "The source page is the absolute authority for identity, hairstyle, clothing design, pose, "
        "anatomy, composition, panel layout, line art, screentone, luminance, and whitespace. "
        "Preserve every glyph, punctuation mark, speech bubble, caption, sound effect, panel border, "
        "screentone pattern, black ink line, facial feature, clothing fold, prop, and background object "
        "in exactly the same position and shape. Change chroma only; do not create any new pixel-level "
        "structure. Add flat base color with restrained cel shading only. Do not invent, remove, clean "
        "up, translate, reconstruct, complete, or redesign any content. If a region or identity is "
        "uncertain, leave the source pixels unchanged."
    )


# 方法说明：按角色档案和当前服装匹配状态生成局部 FLUX.2 提示词。
def build_character_prompt(
    *,
    character_id: str,
    stable_traits: list[str],
    outfit_traits: list[str],
    colors: list[dict[str, object]],
    outfit_matches_reference: bool,
) -> str:
    traits = list(stable_traits)
    if outfit_matches_reference:
        traits.extend(outfit_traits)
    allowed_parts = {
        "hair",
        "left_eye",
        "right_eye",
        "eyebrow",
        "mouth",
        "face_marking",
        "skin",
        "upper_clothing",
        "lower_clothing",
        "inner_clothing",
        "outer_clothing",
        "headwear",
        "hair_accessory",
        "neckwear",
        "gloves",
        "belt",
        "legwear",
        "footwear",
        "jewelry",
        "accessory",
        "prop",
    }
    if not outfit_matches_reference:
        allowed_parts.intersection_update(
            {"hair", "left_eye", "right_eye", "eyebrow", "mouth", "face_marking", "skin"}
        )
    palette = [
        f"{item['part']} RGB{tuple(item['rgb'])}"
        for item in colors
        if item.get("part") in allowed_parts
    ]
    details = ", ".join([*traits[:10], *palette]) or "use the assigned reference identity"
    return (
        f"Character {character_id}, color guidance inside the assigned visible character region only: {details}. "
        "The traits identify existing source regions only and are never instructions to draw them. "
        "Keep the exact hairstyle, face, clothing parts, accessories, pose, contours, folds, and "
        "grayscale shading visible in the source page. Apply reference colors only to matching visible "
        "parts by changing chroma only. This is palette guidance only: do not reconstruct, complete, "
        "replace, or redraw the character, and do not transfer this character's colors to another "
        "person or the background. If the match is uncertain, leave the source unchanged."
    )


# 方法说明：将静态角色档案压缩为只补充已有区域颜色的工作流提示词。
def build_static_character_guide(characters: list[dict[str, object]]) -> str:
    part_labels = {
        "hair": "hair",
        "left_eye": "left eye",
        "right_eye": "right eye",
        "eyebrow": "eyebrows",
        "mouth": "mouth or lips",
        "face_marking": "existing face marking",
        "skin": "skin",
        "upper_clothing": "upper clothing",
        "lower_clothing": "lower clothing",
        "inner_clothing": "inner clothing",
        "outer_clothing": "outer clothing, coat or cape",
        "headwear": "hat or headwear",
        "hair_accessory": "hair accessory",
        "neckwear": "collar, scarf, tie or neckwear",
        "gloves": "gloves or handwear",
        "belt": "belt, sash or buckle",
        "legwear": "stockings, socks, legwear or leg armor",
        "footwear": "shoes, boots or foot armor",
        "jewelry": "jewelry or metal ornament",
        "accessory": "existing accessory",
        "prop": "existing character-held prop",
    }
    stable_parts = {
        "hair",
        "left_eye",
        "right_eye",
        "eyebrow",
        "mouth",
        "face_marking",
        "skin",
    }
    blocks = []
    for item in characters[:3]:
        colors = list(item.get("colors", []))
        stable_palette = [
            f"{part_labels[str(color['part'])]} RGB{tuple(color['rgb'])}"
            for color in colors
            if color.get("part") in stable_parts
        ]
        outfit_palette = [
            f"{part_labels[str(color['part'])]} RGB{tuple(color['rgb'])}"
            for color in colors
            if color.get("part") in part_labels
            and color.get("part") not in stable_parts
        ]
        stable_traits = [str(value) for value in item.get("stable_traits", [])][:8]
        outfit_traits = [str(value) for value in item.get("outfit_traits", [])][:8]
        block = [
            f"Character {item['display_name']} recognition-only anchors: "
            + (", ".join(stable_traits) or "use the supplied reference identity")
            + ". These anchors only identify source regions; never draw, complete, or transfer them."
        ]
        if stable_palette:
            block.append(
                "When this matching character already exists in the source, use: "
                + ", ".join(stable_palette)
                + "."
            )
        if outfit_palette:
            condition = ", ".join(outfit_traits) or "the same visible garment structure"
            block.append(
                f"Only when the source already shows matching clothing or leg features ({condition}), "
                "use: "
                + ", ".join(outfit_palette)
                + ". Otherwise ignore these outfit colors."
            )
        blocks.append(" ".join(block))
    guide = " ".join(blocks) or "No static character palette is available."
    return (
        "CHARACTER PALETTE-ONLY GUIDE. Change chroma only inside confidently matching regions that already "
        "exist in the source manga page. Apply available colors to matching facial details, garment layers, "
        "legwear, footwear, accessories, jewelry, and held props only when those exact parts are visibly "
        "present. This guide must never add, remove, replace, reshape, reconstruct, complete, or move "
        "any hairstyle, eye, face, garment, stocking, leg feature, shoe, armor, accessory, pose, line, "
        "text, panel, background, or object. Do not create new pixels from a reference description. Do not "
        "apply an absent or uncertain character or clothing feature; leave such source pixels unchanged. "
        + guide
    )
