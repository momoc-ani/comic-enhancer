# Qwen3-VL 与 FLUX.2 角色及背景上色调研方案

## 结论

当前 FLUX.2 工作流已经会对人物、物体和背景做整页上色，也能通过最多三张参考图改善人物配色；但它还不能可靠地把某张参考图和漫画页中的对应角色绑定，因此不足以作为“角色跨页稳定、多人不串色、背景自然完整”的最终方案。

主要原因是：

- 当前 `Qwen3 4B Text Encoder` 只编码文本，不是视觉语言模型，不能识别漫画页中的角色，也不能输出角色位置。
- 三张角色参考图通过 `ReferenceLatent` 进入同一套全局 conditioning，没有角色 ID、实例区域或掩码绑定。
- 后端目前只向工作流传递参考图字节；角色名称、摘要、身份和参考图槽位语义没有保留到工作流。
- 参考图不足三个槽位时会重复最后一张图，可能无意中增加某个角色的影响权重。
- 现有全局提示词已经要求为 `skin, hair, clothing, objects and backgrounds` 应用自然动漫配色，但没有页面级场景理解，也无法区分“角色专属颜色”和“环境推断颜色”。
- 当前三页数据只证明链路可运行。按照项目门禁，至少需要 100 页授权数据集才能判断最终画质、性能和稳定性。

推荐增加独立的 `Qwen3-VL-4B-Instruct` 视觉分析服务。它负责从漫画原图和有序角色参考图中识别角色、定位角色实例、提取有证据的颜色特征，并描述背景场景；现有 Qwen3 4B 文本编码器继续负责把整理后的提示词编码给 FLUX.2。两者职责不能混为一体。

最终推荐的生成结构是：在同一次 FLUX.2 去噪中，用全局场景 conditioning 覆盖整页和背景，用每个角色自己的 conditioning 与参考图约束其区域，同时继续使用原图 latent、文字和墨线保护。这样比“先生成背景、再分别生成角色并拼接”更容易保持统一光照、色调和边缘连续性。

## 什么样的效果才算好

### 角色

- 同一角色在同页不同分格、相邻页面和不同姿态下保持发色、瞳色、肤色及标志性服装颜色一致。
- 多角色同框时，每个角色只使用自己的参考图和颜色档案，不把角色 A 的发色或服装颜色套到角色 B。
- 遮挡、远景和小脸等低置信度实例宁可只接受全局自然上色，也不强制套用错误身份。
- 参考图没有证据的颜色不由视觉模型臆测为“官方设定”；未知字段应省略。

### 背景、物体和环境

- 背景不能只做单一色洗，应根据天空、室内、植被、木材、金属、道路和光源等语义选择合理颜色。
- 同一分格中的人物、道具和背景共享一致的时间、光源方向、色温与明暗关系。
- 多个连续分格属于同一场景时，应尽量复用场景调色板，避免墙壁、天空或家具在相邻页无理由变色。
- 没有彩色场景参考时，背景颜色只能称为“合理推断”，不能宣称还原原作官方配色。

### 漫画结构

- 原有文字、标点、数字、气泡、拟声词、网点、分格边界、人物轮廓和构图保持不变。
- 颜色应进入已有封闭区域，不新增人物、五官、物体、纹理或文字。
- 颜色层次要足以区分人物、前景和背景，但不能以高饱和度覆盖墨线和原有明度结构。
- 任一分析或推理阶段失败时保留原图，不能让阅读链路失效。

## 模型职责与调研结果

### FLUX.2 Klein 4B

FLUX.2 Klein 4B 是 Apache-2.0 的 4B rectified-flow 图像生成与编辑模型，支持多参考图编辑。项目当前使用 FLUX.2 Klein 4B FP8、FLUX.2 VAE、Qwen3 4B FP8 文本编码器和 `ReferenceLatent`，以原图 latent 进行八步、`0.85` 去噪的整页上色。

BFL 官方的 prompt upsampling 指南明确使用视觉语言模型扩充复杂提示词，因此“先由 VLM 理解图像，再把结构化结果交给 FLUX.2”与官方推荐方向一致。不过 prompt upsampling 只能增加语义信息；如果没有区域或掩码约束，更多角色文字仍不能保证提示词只作用于对应角色。

官方模型卡写明约需 13GB VRAM，官方仓库 README 中也出现约 8GB VRAM 的说明。这些数字可能来自不同精度、卸载策略或统计口径，不能据此假设 FLUX.2 和 Qwen3-VL 可以在同一张显卡上同时常驻，必须以目标 RTX 4090 的峰值实测为准。

### 当前 Qwen3 4B 文本编码器

工作流中的 `qwen_3_4b_fp8_mixed.safetensors` 由 `CLIPLoader` 以 `flux2` 类型加载，用途是编码正向和负向文本。它不会读取漫画图片，也不会完成角色识别、视觉 grounding 或颜色提取。

因此，不应把现有节点描述成“通过视觉大模型获取角色提示词”。视觉分析必须由一个真正支持图像输入的模型先完成，再将经过校验和压缩的文本写入 `CLIPTextEncode`。

### Qwen3-VL-4B-Instruct

Qwen3-VL-4B-Instruct 支持多图输入、动漫内容识别和 2D grounding，适合同时接收当前漫画页和有序角色参考图，并返回角色实例与位置。模型采用 Apache-2.0 许可证。

建议优先使用 Instruct 版本而不是让视觉模型直接输出长篇自由文本，原因是本项目需要稳定的 JSON 契约、低温确定性输出和严格失败回退。官方 Qwen3-VL 仓库提供多图输入、图片编号以及 boxes/points 定位能力，并推荐使用 vLLM 或 SGLang 部署。

`Qwen3-VL-4B-Instruct-FP8` 模型卡说明其 FP8 使用 128 block size，并说明 Transformers 不能直接加载该 FP8 版本，推荐 vLLM 或 SGLang。部署时不能直接假设它可以复用当前 ComfyUI Python 环境。

## 本次推荐工作流架构

### 服务拓扑

```text
Chrome 扩展
  -> Comic Enhancer API
       |- 元数据与角色参考服务
       |- CharacterVisionAnalyzer 窄接口
       |    -> 本地 Qwen3-VL sidecar
       |- CharacterPromptPlanner（确定性模板与校验）
       |- Flux2WorkflowBinder（提示词、参考图、区域/掩码绑定）
       |- GPU 调度器（VLM 与 ComfyUI 串行或分卡）
       -> 唯一 ComfyUI
            -> FLUX.2 Klein 4B 工作流
```

插件仍只访问 Comic Enhancer API，不感知 Qwen3-VL 或 ComfyUI。API 仍只配置一个 ComfyUI 地址；Qwen3-VL 是视觉分析能力，不是第二个生成后端。`CharacterVisionAnalyzer` 应定义为窄接口，便于替换 4B/2B、本机/独立内网部署，而不让模型 SDK 侵入任务编排和工作流加载器。

### API 编排顺序

一次页面处理建议按以下顺序执行：

1. 根据作品身份读取已缓存角色参考图，并保留角色 ID、名称、摘要、来源、槽位和图像 SHA-256。
2. 对参考图生成或读取角色颜色档案；此步骤按角色低频执行，不在每页重复提取。
3. 将当前漫画页、有序参考图及其明确映射发送给 Qwen3-VL，得到分格、角色实例位置、置信度和场景描述。
4. 严格校验 JSON 与坐标，只保留高置信度角色实例；单个角色失败只降级该角色，不使整页失败。
5. `CharacterPromptPlanner` 用固定模板生成一份全局场景提示和最多三份角色提示，禁止直接透传 VLM 长文本。
6. `Flux2WorkflowBinder` 克隆已校验的完整工作流，绑定原图、实际参考图、提示词和区域/掩码，然后提交唯一 ComfyUI 队列。
7. FLUX.2 在一次采样中完成角色、背景和物体上色，之后恢复原文字与关键墨线并做 Lanczos 2x 输出。
8. API 校正输出几何，记录实际分析版本、绑定计划、回退路径和缓存元数据，再返回不可变 WebP。

网络元数据刷新仍不得阻塞当前页面。只有本地已有角色参考图才进入当前推理；缺失的元数据和角色图继续后台刷新，供后续页面使用。

### ComfyUI 节点结构

推荐保留当前原图结构分支，只重构 conditioning 分支：

```text
INPUT_IMAGE
  -> ImageScaleToTotalPixels
  -> VAEEncode -------------------------------------------> Sampler latent_image
       |
       +-> ReferenceLatent（全局源图结构）

GLOBAL_SCENE_PROMPT
  -> CLIPTextEncode
  -> ReferenceLatent（原图 latent）
  --------------------------------------------------------> 基础 positive

CHARACTER_1_PROMPT -> CLIPTextEncode
REFERENCE_IMAGE_1  -> Scale -> VAEEncode -> ReferenceLatent
CHARACTER_1_MASK   -> ConditioningSetMask/Area
  --------------------------------------------------------> 角色 1 positive

CHARACTER_2_PROMPT -> CLIPTextEncode
REFERENCE_IMAGE_2  -> Scale -> VAEEncode -> ReferenceLatent
CHARACTER_2_MASK   -> ConditioningSetMask/Area
  --------------------------------------------------------> 角色 2 positive

CHARACTER_3_PROMPT -> CLIPTextEncode
REFERENCE_IMAGE_3  -> Scale -> VAEEncode -> ReferenceLatent
CHARACTER_3_MASK   -> ConditioningSetMask/Area
  --------------------------------------------------------> 角色 3 positive

基础 positive + 各角色 positive
  -> ConditioningCombine
  -> CFGGuider
  -> Flux2Scheduler / SplitSigmasDenoise / SamplerCustomAdvanced
  -> VAEDecode
  -> 原图文字与关键墨线蒙版恢复
  -> Lanczos 2x
  -> SaveImage
```

首版区域绑定优先使用百分比矩形，验证节点组合确实对当前 FLUX.2 生效后，再接入 mask。角色有多个实例时，为每个实例设置区域，但共用同一角色提示和参考图。区域边界需要适度扩张以覆盖头发、皮肤和服装；不能只框脸，也不能跨越分格。

负向 conditioning 保留全局结构保护内容。角色专属排除词是否也做区域绑定，需要通过 A/B 验证；在没有实测前，不应同时堆叠大量全局负面颜色词，因为它可能抑制其他角色或背景本来正确的颜色。

### 背景上色策略

第一版不单独生成背景，也不默认建立背景反向掩码。全局场景 conditioning 覆盖整页，明确要求所有已有背景和物体区域采用自然、分层、与光照一致的颜色；角色 conditioning 只在局部增加身份配色约束。

这个结构能够避免两类问题：

- 如果只使用角色 mask，mask 外区域可能缺少足够的上色语义，背景容易继续灰白。
- 如果把人物和背景分两次生成再拼接，人物边缘、遮挡、反光和环境色容易不一致。

只有发现全局场景提示仍把角色颜色传播到背景时，才增加“角色掩码并集的反向 mask”作为背景 conditioning 范围。即使增加背景 mask，也应在同一次采样中与角色 conditioning 合并，而不是拆成两次生成。

### 回退层级

```text
Qwen3-VL 正常且区域计划有效
  -> 区域角色 conditioning + 全局场景 conditioning

区域节点不兼容或区域计划无效
  -> Qwen3-VL 动态全局提示 + 当前全局参考图

Qwen3-VL 超时、不可用或 JSON 无效
  -> 当前 FLUX.2 固定提示词 + 当前参考图流程

FLUX.2 失败
  -> 现有质量档

所有生成档失败
  -> 保留原图
```

回退必须逐级记录真实执行路径，返回的 `model_profile` 和缓存 revision 必须反映实际结果。不能让一次低置信度角色识别把整个页面直接降到质量档；应先丢弃该角色的局部 conditioning，让全局场景提示继续完成背景和普通上色。

## 推荐处理链路

```text
角色彩色参考图
  -> Qwen3-VL 角色档案提取（低频，可缓存）
  -> 有证据的发色、瞳色、肤色、服装和标志物

当前灰度漫画页 + 有序角色档案/参考图
  -> Qwen3-VL 页面分析（按页，可缓存）
  -> 分格、角色身份、实例区域、场景和背景描述
  -> Pydantic 严格校验、置信度过滤、确定性模板压缩

全局场景提示 -------------------------------> 整页 conditioning
角色 A 提示 + 角色 A 参考图 + A 的区域/掩码 ---> 局部 conditioning
角色 B 提示 + 角色 B 参考图 + B 的区域/掩码 ---> 局部 conditioning
                                                    |
原始漫画页 -> 原图 latent + 文字/墨线保护 ----------+-> 单次 FLUX.2 去噪 -> 2x 输出
```

全局场景提示负责页面结构、自然光照、背景、物体和没有可靠身份的区域。角色提示只包含稳定身份颜色，不描述背景。各角色 conditioning 只绑定自己的参考图和区域。这样既能让整页完整上色，也能减少角色之间以及角色和背景之间的颜色串扰。

## 视觉分析契约

建议把视觉分析拆成“角色档案”和“页面计划”两个结果，而不是每页都让模型重新解释全部参考图。

### 角色档案

角色档案只从已确认的彩色参考图和可信元数据提取，按角色缓存：

```json
{
  "character_id": "work:example:character-a",
  "display_name": "角色 A",
  "reference_sha256": "...",
  "positive_traits": [
    "black hair",
    "amber eyes",
    "navy school uniform",
    "red ribbon"
  ],
  "negative_traits": [
    "blonde hair",
    "blue eyes",
    "colors from other characters"
  ],
  "evidence": {
    "hair": "reference_image",
    "eyes": "reference_image",
    "outfit": "reference_image"
  }
}
```

字段只允许使用受控词表和短语；不得保存视觉模型生成的故事、性格或无关描述。模型看不清的颜色字段应为空。

### 页面角色与场景计划

Qwen3-VL 输入中的图片顺序必须显式声明，例如 `Picture 1 = manga page`、`Picture 2 = character-a reference`。输出使用严格 JSON：

```json
{
  "scene": {
    "location": "school corridor",
    "time": "daylight",
    "lighting": "soft neutral daylight",
    "background_positive": [
      "pale walls",
      "muted floor",
      "natural material colors"
    ],
    "background_negative": [
      "single-color wash",
      "neon background",
      "colors copied from character clothing"
    ]
  },
  "characters": [
    {
      "character_id": "work:example:character-a",
      "reference_slot": 1,
      "visible": true,
      "instances": [
        {
          "panel_id": 1,
          "box": [120, 80, 430, 760],
          "confidence": 0.93
        }
      ]
    }
  ]
}
```

服务端应把模型坐标统一转换为原图坐标，裁剪到图像边界，并拒绝空框、反向框和异常大框。角色颜色仍从已缓存的角色档案读取，不允许灰度漫画页决定官方颜色。页面分析只负责身份匹配、定位和背景语义。

## 分阶段实施

### 第一阶段：动态全局角色提示词

第一阶段用于低风险验证视觉分析是否准确：

- 增加独立的 `CharacterVisionAnalyzer` 窄接口及 Qwen3-VL sidecar 实现。
- 保留角色 ID、名称、摘要、参考图槽位和图像字节，不再把角色库立即降级为无语义的 `dict[str, bytes]` 值序列。
- Qwen3-VL 生成严格页面计划，服务端通过固定模板把已识别角色档案和场景描述追加到现有全局 `Colorization Instruction`。
- 保留当前全局 `ReferenceLatent`、原图 latent、文字/墨线保护和尺寸恢复逻辑。
- 取消“重复最后一张参考图填满槽位”；空槽位应由工作流显式支持，或使用不参与 conditioning 的中性占位输入。
- Qwen3-VL 超时、JSON 无效、角色置信度不足或服务不可用时，回退到当前 FLUX.2 流程并记录不含受保护内容的原因。

该阶段可以改善提示词完整度和背景语义，但仍是全局 conditioning，不能视为已解决多人串色。它只用于验证 VLM 识别质量和提示词收益。

### 第二阶段：角色区域绑定

第二阶段是实现角色稳定性的关键：

```text
全局场景 prompt
  -> CLIPTextEncode
  -> 覆盖整页的基础 conditioning

单个角色 prompt
  -> CLIPTextEncode
  -> ReferenceLatent（只连接该角色参考图）
  -> ConditioningSetAreaPercentage 或 ConditioningSetMask
  -> 与基础 conditioning 合并
```

- 同一角色在多个分格出现时可生成多个实例区域，共用同一角色档案和参考图。
- 角色区域应适当包含头发、皮肤和服装，避免只框脸部；重叠角色需要单独处理置信度和遮挡顺序。
- 全局 conditioning 始终覆盖整页，负责背景、道具、环境光和未识别人物，因此局部约束不会留下未上色区域。
- 可选的背景 conditioning 使用角色区域并集的反向掩码，但应先验证 ComfyUI 节点与 FLUX.2 conditioning 的实际兼容性。默认优先使用整页全局场景提示，减少硬边界。
- 需要验证 `ConditioningSetAreaPercentage`、`ConditioningSetMask`、`ConditioningCombine` 和 `ReferenceLatent` 在当前 FLUX.2 八步采样下的组合行为，不能只根据节点存在就认定效果可靠。

推荐保持一次统一去噪。分别生成背景和人物再合成会增加边缘接缝、光照不一致、遮挡错误和文字二次损坏的风险，只适合作为失败后的实验分支。

### 第三阶段：精细角色掩码

如果矩形区域仍造成背景串色或多人重叠错误，再增加精细分割：

- 使用 Qwen3-VL 的 boxes 或 points 作为 SAM2 等分割模型的提示。
- 将分割结果限制在原角色框和对应分格内，避免跨分格扩散。
- 用 `ConditioningSetMask` 替换矩形区域，并在角色边缘保留适度羽化。
- 文字、气泡、分格线和关键墨线保护掩码的优先级高于角色掩码。

该阶段会新增模型、依赖、显存和延迟，不建议在第一阶段直接引入。

### 第四阶段：跨页场景调色板

角色稳定后，如果同一地点在相邻页仍频繁变色，可增加场景调色板缓存：

- 以作品 ID、章节 ID、相邻页场景签名和已确认场景参考图形成场景键。
- 缓存墙面、天空、家具、制服群体和主光色温等稳定字段。
- 只在场景相似度和置信度均达标时复用；镜头切换、时间变化或闪回必须新建场景。
- 没有彩色证据时允许保持“视觉连续”，但元数据中必须区分推断色与已确认颜色。

## 提示词组织

服务端应使用固定模板生成简短英文提示词，而不是直接把 VLM 的长文本交给 FLUX.2。建议结构如下：

```text
[固定结构保护]
Colorize all existing regions of the manga page without redrawing.

[全局场景]
Scene: school corridor, soft neutral daylight, pale walls, muted floor,
natural anime colors for all existing objects and background regions.

[角色区域]
Character work:example:character-a in the assigned region only:
black hair, amber eyes, navy school uniform, red ribbon.

[排除项]
Do not transfer this character's colors to another character or background.
Preserve text, bubbles, panel borders, screentone and line art.
```

提示词需要限制角色数量和长度。当前参考图上限为三个，第一版建议最多处理三个高置信度角色，每个角色只保留能直接影响配色的稳定特征。服装随剧情变化时，应优先使用当前页可验证的服装类别，并避免把某套参考服装强制应用到所有页面。

## 缓存与确定性

### 角色档案缓存

```text
key = character_id
    + reference_image_sha256
    + vlm_model_id_and_revision
    + character_profile_template_revision
```

### 页面角色计划缓存

```text
key = source_image_sha256
    + ordered_character_profile_digests
    + ordered_reference_slots
    + vlm_model_id_and_revision
    + page_analysis_template_revision
```

### 最终结果缓存

现有结果缓存键还必须加入：

- 视觉分析功能开关和分析版本；
- 实际角色档案摘要及槽位顺序；
- 页面角色实例区域或掩码摘要；
- 场景调色板版本；
- 实际回退路径和 FLUX.2 工作流缓存版本。

Qwen3-VL 使用 `temperature=0`、固定 seed、固定图片顺序和严格 JSON Schema。模型升级、prompt 模板变化、区域策略变化或结构保护变化都必须更新对应 revision，避免复用旧结果。

## 部署与资源风险

推荐把 Qwen3-VL 部署为本地 `qwen-vl` sidecar，仅由 Comic Enhancer API 通过窄接口访问。Chrome 插件仍只连接唯一的 Comic Enhancer API；现有唯一 `ComfyUI URL` 约束不变，Qwen3-VL sidecar 不是第二个 ComfyUI。

单张 RTX 4090 同时常驻 FLUX.2、Qwen3 4B 文本编码器、Qwen3-VL 和其他候选模型可能 OOM。调度优先级建议如下：

1. 优先使用第二张 GPU 或独立可信内网推理机运行 Qwen3-VL。
2. 单卡部署先采用同一 GPU 调度器串行分析与生成，并实测模型卸载、重新加载和缓存收益。
3. 如果 4B 视觉模型仍不稳定，再把 Qwen3-VL 2B 作为降级实验，但必须重新验证角色识别和 grounding 精度。

不能让 Qwen3-VL sidecar 和 ComfyUI 绕过统一 GPU 并发控制同时抢占显存。视觉分析超时应短于页面推理总超时，并允许直接走当前 FLUX.2 回退路径。

漫画页和第三方角色图不得发送到公网视觉 API。第三方角色图仍只能进入运行时缓存，不能自动提交、镜像或重新发布。

## 验收方案

### 对照组

- A：当前 FLUX.2 全局提示词和全局参考图。
- B：Qwen3-VL 页面分析加动态全局角色/场景提示词。
- C：Qwen3-VL 页面分析加角色区域提示词和角色专属参考图绑定。
- D：仅在 C 仍有明显串色时，增加精细角色掩码。

初期用 30 至 50 页授权样本做方案筛选，样本应覆盖两至三个重复角色、多人同框、服装变化、室内外、昼夜、远景、遮挡和跨分格角色。最终准入仍使用至少 100 页授权数据集，三页冒烟结果不能用于宣布达标。

### 必须记录

- 已知角色的识别 precision、recall 和错误身份率；
- bbox 与人工标注的 IoU，使用 mask 时增加 mask IoU；
- 发色、瞳色、肤色和标志性服装颜色的跨页一致性；
- 不同角色之间以及角色与背景之间的颜色串扰率；
- 背景完整上色率、同场景跨页颜色漂移和明显单色洗比例；
- 文字、气泡、拟声词、网点、墨线、构图和原图明度保持；
- 冷启动、热模型、VLM 缓存命中和最终结果缓存命中耗时；
- ComfyUI 与 Qwen3-VL 的 GPU 显存峰值、进程 RSS 和连续任务后的增长趋势；
- 每页实际使用的角色档案、区域策略、分析 revision、工作流 revision 和回退原因。

低置信度结果必须回退，不能为了提高角色覆盖率强行应用颜色。建议先用 30 至 50 页结果校准置信度、IoU 和串扰门槛，再由用户审核最终数值；未经 100 页门禁，不应把任何候选切换为默认最高质量路线。

## 需要用户审核的决策

实现前需要确认以下架构与资源决策：

- Qwen3-VL 使用独立 GPU、独立内网机器，还是与 ComfyUI 在 RTX 4090 上串行卸载。
- 第一阶段是否只作为隐藏实验开关，还是新增用户可见的处理档位。
- 第二阶段先验证矩形 conditioning，还是直接承担 SAM2 依赖与显存成本。
- 背景仅使用全局场景提示，还是增加场景调色板缓存与跨页复用。
- 角色档案中哪些颜色可视为已确认事实，以及服装变化时的覆盖规则。

在这些决策和 A/B/C 基准结果完成审核前，不修改默认工作流，不把当前方案描述为已经足够稳定。

## 官方资料

- [FLUX.2 Klein 4B 模型卡](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
- [Black Forest Labs FLUX.2 官方仓库](https://github.com/black-forest-labs/flux2)
- [FLUX.2 prompt upsampling 指南](https://github.com/black-forest-labs/flux2/blob/main/docs/flux2_with_prompt_upsampling.md)
- [Qwen3-VL-4B-Instruct 模型卡](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct)
- [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)
- [Qwen3-VL-4B-Instruct-FP8 模型卡](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-FP8)
- [ComfyUI `ReferenceLatent` 实现](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_edit_model.py)
- [ComfyUI `ConditioningSetArea` 与 `ConditioningSetMask` 实现](https://github.com/Comfy-Org/ComfyUI/blob/master/nodes.py)

资料核对日期：2026-08-15。
