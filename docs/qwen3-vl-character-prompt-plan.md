# Qwen3-VL 角色静态调色档方案

## 结论

新增独立 `flux2_character` 档位，用 Qwen3-VL 建立作品级角色静态档案，再把确定性 RGB 调色信息补充到 FLUX.2 的正向提示词。Qwen3-VL 不负责重写漫画、不负责场景理解，也不在每一页重复分析。

这条链路解决的是角色跨页颜色统一和多人不串色：

- 参考图分析只发生在角色档案缺失或版本变化时，并按角色缓存。
- Pillow 根据 Qwen3-VL 给出的参考图区域确定性采样 RGB，不采纳模型自由生成的颜色名称。
- 每页只读取静态角色档案，追加到 `Colorization Instruction`，不生成页面 bbox、mask 或页面级 VLM 计划。
- FLUX.2 仍以原图为页面结构权威，只给原图已有区域上色；工作流直出后继续本地几何恢复和 Real-CUGAN 放大。
- 任何阶段失败都保留原图；本档位不静默回退到其他模型档位。

## 边界

Qwen3-VL 可以提取“参考图中看得到的部件结构和颜色证据”，但不能改变漫画页内容。提示词中的硬性约束为：

```text
Color only matching regions that already exist in the source manga page.
The source page is authoritative for identity, hairstyle, anatomy, clothing shape,
pose, line art, text, panel layout, objects and background.
Never add, remove, replace, reshape, reconstruct or move any person, garment,
leg feature, shoe, accessory, prop, line, glyph, panel or object.
```

因此角色档案只提供调色补充，不提供“重画角色”的指令。漫画页中没有的帽子、丝袜、鞋、首饰、道具或身体标记必须忽略，不能因为参考图存在就强行生成。

## 服务拓扑

```text
Chrome 扩展
  -> Comic Enhancer API
       |- Flux2CharacterModeStrategy
       |- CharacterLibraryBuilder
       |    |- SQLite 角色档案与内容寻址参考图
       |    |- CharacterVisionAnalyzer 窄接口
       |    `-> Qwen3-VL sidecar（独立服务）
       |- 静态 CharacterPromptContext 缓存
       `-> 唯一 ComfyUI
            `-> flux2-klein-4b-qwen3-vl-character-colorize.json
                 -> FLUX.2 直出
                 -> 几何恢复与 Real-CUGAN 二阶段放大
```

插件只能访问 Comic Enhancer API，不能直连 Qwen3-VL、ComfyUI 或结果地址。Qwen3-VL 是视觉分析 sidecar，不是第二个生成后端；Comic Enhancer API 仍只配置一个 ComfyUI 地址。

## 完整角色颜色区域

角色调色板不是“头发、眼睛、腿和鞋”的窄列表，而是受控的可见部件集合。当前 `RegionPart` 支持：

| 类别 | 作用 |
| --- | --- |
| `hair` | 头发主体、刘海、发尾等已有头发区域 |
| `left_eye` / `right_eye` | 左右眼虹膜、瞳色；不改变眼睛线稿 |
| `eyebrow` | 眉毛已有颜色 |
| `mouth` | 嘴唇或嘴部已有颜色 |
| `face_marking` | 原图已有纹身、胎记、脸部涂装等标记 |
| `skin` | 脸、手、腿等可见皮肤 |
| `upper_clothing` / `lower_clothing` | 上衣、裙、裤、短裤等主体服装 |
| `inner_clothing` | 衬衣、内搭、领口露出的内层 |
| `outer_clothing` | 外套、斗篷、披风、围巾外层等 |
| `headwear` | 帽子、头盔、发箍等头部覆盖物 |
| `hair_accessory` | 发夹、发带、蝴蝶结等固定发饰 |
| `neckwear` | 领带、领结、项圈、围巾等领饰 |
| `gloves` | 手套、护腕、手部装甲 |
| `belt` | 腰带、腰封、腰包、饰扣 |
| `legwear` | 袜子、丝袜、裤袜、护腿、腿甲 |
| `footwear` | 鞋、靴子、凉鞋、足部装甲 |
| `jewelry` | 耳环、项链、戒指、金属饰件 |
| `accessory` | 其他已存在且稳定的角色配饰 |
| `prop` | 角色明确持有且颜色稳定的物件 |

Qwen3-VL 只能返回看得清且属于上述词表的区域。没有证据的部件不返回；多个候选区域相同类别只保留最高置信度区域。`ProfileRegion` 坐标为参考图的 0 到 1000 百分比框，服务端再用 Pillow 采样主色并记录置信度和参考图 SHA-256。

## 处理流程

### 1. 角色档案准备

`CharacterLibraryBuilder.prepare_prompt_context()` 接收作品键和有序角色参考图：

1. 内容寻址保存参考图并计算 SHA-256。
2. 用 `character_id + reference_sha256 + Qwen3-VL revision + PROFILE_TEMPLATE_REVISION` 查询缓存。
3. 未命中时，sidecar 只分析该角色参考图，返回稳定结构、服装结构和 `regions`。
4. Pillow 在每个区域内确定性采样 RGB，生成 `CharacterProfile.colors`。
5. 组合最多三个 `PreparedCharacter`，形成 `CharacterPromptContext`。

该方法不会接收漫画页字节，也不会调用 `analyze_page()`。代码仍保留页面分析契约供旧实验和未来可选能力使用，但 `flux2_character` 生产路径不调用它。

### 2. 每页处理

每页只做以下动作：

1. 按作品键和参考图内容读取已缓存的 `CharacterPromptContext`。
2. 从角色档案生成固定英文 `CHARACTER COLOR GUIDE`，按角色列出完整可用 RGB 调色板。
3. 将指南追加到工作流唯一的 `Colorization Instruction` 节点；不把 VLM 自由文本直接透传给 FLUX.2。
4. 上传原图到 `INPUT_IMAGE`，将最多三张角色参考图绑定到 `REFERENCE_IMAGE_1/2/3`；未使用槽位绑定当前原图，避免重复使用某个角色图。
5. 提交完整 FLUX.2 工作流，直出漫画页结果。
6. 执行现有几何恢复和本地 Real-CUGAN 二阶段放大，保存不可变 WebP。

这里没有页面级 Qwen3-VL 调用、角色 bbox、`ConditioningSetMask` 或本地明度/文字/墨线回注。页面结构保护由原工作流的源图、正向结构约束和负向 conditioning 完成，最终质量必须通过授权样本验收。

## Qwen3-VL 角色档案契约

输入只包含一张已确认的彩色角色参考图及角色 ID。提示词要求：

- 只分析外观结构、服装结构和可采色区域；不描述剧情、场景、性格或姿势。
- `stable_traits` 只写发型轮廓、固定配饰、眼睛形状等跨服装特征。
- `outfit_traits` 只写参考图可见的衣物、层次和配饰结构，不写颜色名称。
- `regions` 每项只能使用上面的 `RegionPart`，并返回 `box_2d`、`confidence`、`structural_trait`。
- `structural_trait` 是短结构词，不是重绘指令；禁止猜测看不清的颜色。

示例：

```json
{
  "character_id": "work:example:character-a",
  "stable_traits": ["long straight hair"],
  "outfit_traits": ["layered high-collar uniform", "ankle boots"],
  "regions": [
    {"part": "hair", "box_2d": [100, 50, 800, 650], "confidence": 0.98, "structural_trait": "long straight hair"},
    {"part": "left_eye", "box_2d": [340, 330, 430, 430], "confidence": 0.92, "structural_trait": "visible left iris"},
    {"part": "inner_clothing", "box_2d": [320, 500, 680, 720], "confidence": 0.88, "structural_trait": "white inner shirt"},
    {"part": "legwear", "box_2d": [300, 700, 700, 980], "confidence": 0.91, "structural_trait": "opaque stockings"},
    {"part": "footwear", "box_2d": [250, 900, 760, 1000], "confidence": 0.94, "structural_trait": "ankle boots"}
  ]
}
```

## 静态提示词组织

`build_static_character_guide()` 生成的内容由三层组成：

1. 固定边界：只给源图已有区域上色，禁止新增、删除、替换、重塑或移动任何结构。
2. 角色锚点：稳定特征和有证据的发色、眼色、肤色、眉眼、面部标记。
3. 完整可见服装调色板：服装各层、头饰、发饰、领饰、手套、腰带、腿部穿着、鞋靴、首饰、配饰和持有物。

服装颜色附带“只有源图已经出现匹配服装结构时才使用”的条件，避免把参考图的整套服装强制套到换装页面。提示词不包含场景描述，不要求模型生成新的角色细节。

## 工作流契约

文件：`workflows/flux2-klein-4b-qwen3-vl-character-colorize.json`

必须满足：

- 是自包含 ComfyUI API JSON，不含 `${...}` 占位符。
- 唯一正向提示节点标题为 `Colorization Instruction`。
- 存在 `REFERENCE_IMAGE_1/2/3`，并能被加载器稳定发现。
- 保留基础工作流的 `ReferenceLatent`、`EmptyFlux2LatentImage`、`Flux2Scheduler` 和 `SaveImage`。
- 不包含 `ConditioningSetMask`、页面 bbox 输入或角色分支 mask。
- 输出前缀为 `comic-enhancer/flux2-character`。

策略只绑定输入图和提示词，不修改节点拓扑；工作流失败时 `Flux2CharacterModeStrategy` 直接失败，插件继续显示原图。

## 缓存和版本

角色档案键：

```text
work_key
+ character_id
+ reference_image_sha256
+ qwen_model_revision
+ PROFILE_TEMPLATE_REVISION
```

角色提示上下文键：

```text
work_key
+ ordered_reference_character_ids
+ ordered_reference_image_sha256
+ qwen_model_revision
+ profile_digest
```

最终页面结果还必须包含原图 SHA-256、`flux2_character` 模式、工作流 revision、`PROMPT_PLANNER_REVISION`、角色上下文 digest、实际 `model_profile` 和外层放大版本。角色档案不包含漫画页哈希，因此可安全跨页复用；页面结果不能跨原图复用。

模型、模板、颜色区域词表、工作流或放大算法变化时，必须更新 revision，避免旧颜色结果被错误复用。

## 部署

推荐把 Qwen3-VL 部署成独立 `qwen-vl` sidecar：

- Comic Enhancer API 通过 Bearer Token 和窄 HTTP 接口访问。
- Chrome 插件只访问 Comic Enhancer API。
- sidecar 与 ComfyUI 可分卡运行；单卡时通过调度器串行运行，不能同时抢占显存。
- Qwen3-VL GGUF/ROCm 运行时和 FLUX.2 的 Python 环境分离；sidecar 只暴露健康检查和角色档案分析接口。
- 参考图和漫画页不得发送到公网视觉 API，第三方角色图只进入运行时缓存。

12GB 显存机器应优先让视觉模型单独运行；FLUX.2 与 Qwen3-VL 同卡常驻需要按实际量化、上下文长度和卸载策略实测，不能仅按参数量估算。

## 日志

关键日志统一使用：

```text
功能=<功能名> 参数=<安全JSON> 结果=<安全JSON> 耗时_ms=<关键功能耗时>
```

需要记录角色档案缓存命中、Qwen3-VL 参考图分析、静态提示上下文准备、ComfyUI 提交、FLUX.2 直出、几何恢复和 Real-CUGAN 放大。不得记录 Token、图片字节或提示词全文。

## 验收

先用 30 至 50 页授权样本筛选，再用至少 100 页授权数据集准入。样本应覆盖：多人同框、角色换装、远景和小脸、遮挡、帽子/发饰、眼睛和面部标记、内外套层次、裙裤袜鞋、首饰和手持物。

必须观察：

- 同一角色跨页发型、双眼、肤色、服装各层和配饰颜色一致。
- 角色 A 的颜色不串到角色 B、背景或文字。
- 参考图没有的部件不会被新增；原图线稿、文字、气泡、分格、物体和构图不被重塑。
- FLUX.2 直出和本地放大各自记录冷启动、热模型和缓存命中耗时。
- Qwen3-VL 参考图分析次数应随角色档案缓存稳定，不随漫画页数增长。

旧的页面 bbox/mask 方案只能作为离线实验，不属于当前 `flux2_character` 生产契约。

资料核对日期：2026-08-15。
