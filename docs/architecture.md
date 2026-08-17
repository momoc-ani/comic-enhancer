# 系统架构

## 页面处理流程

```text
拷贝漫画页面
  -> 拷贝漫画适配器提取作品 ID、章节密文和下一话链接
  -> 调度器优先当前可视页，再按页码预生成当前话和紧邻下一话
  -> 插件后台下载原图
  -> 唯一漫画增强 API 选择上色工作流或 Real-CUGAN 放大处理器
  -> 上色档向唯一 ComfyUI 容器提交对应工作流；放大档调用当前平台原生资源
  -> 执行结果返回 API 做必要的尺寸恢复、缓存和鉴权
  -> 缓存不可变的 WebP 结果和推理元数据
  -> 插件后台鉴权取图并覆盖显示
  -> 任一步失败都保留原图
```

拷贝漫画章节的完整图片列表由页面 `contentKey` 使用 AES-CBC 加密。插件只在站点适配器内通过
浏览器原生 Web Crypto 解密该列表，不执行远端页面脚本。当前话预生成任务按页码串行提交；当前话
队列完成后才处理紧邻下一话。下一话任务只生成服务端缓存，不提前下载增强结果，用户进入下一话后
再按视口优先级读取缓存并覆盖显示。设置或处理档位变化会废弃旧队列，且不会递归预生成下下话。

浏览器插件只访问漫画增强 API。快速、质量和 FLUX.2 都是同一个 ComfyUI 容器中的预设工作流；放大档由 API 进程调用当前平台的 Real-CUGAN 可执行程序。FLUX.2 输出还会进入 UPSCALE 二阶段。后端只配置一个 `comfyui_url`，Real-CUGAN 使用本地资源目录而不是第二个推理地址。

## 档位策略边界

API 先通过 `RoutedInferenceBackend` 区分平台原生档位与主推理后端；ComfyUI 后端再通过 `ComfyUIModeStrategy` 注册上色档位。每个档位独立实现可用性、缓存版本、适配器策略和处理逻辑：

- 快速档和质量档复用同一个预设策略实现，只替换完整工作流；
- 放大档只在显式启用且当前平台的 Real-CUGAN 可执行文件、`models-se/up2x-no-denoise.param` 和 `.bin` 齐全时可用，不选择角色参考图；
- FLUX.2 独立管理最多 3 张参考图，使用旧基准的 `0.85MP` 四步空 latent 工作流输出上色结果；最高质量档在工作流末端恢复原图宽高，再交给 UPSCALE 策略执行二阶段 Real-CUGAN 放大。API 不复用 SD1.5 的后端业务后处理，量化实验档保持独立输出契约。
- 角色线稿保真档独立管理最多 3 张角色参考图，使用同样的 `0.85MP` 四步空 latent 工作流；ComfyUI 恢复原图尺寸后，服务端采用 FLUX.2 的明度和色度，仅回注原图深色墨线，再交给 UPSCALE 策略执行一次 Real-CUGAN 2x。该档位强制跳过角色档直出开关，不改变其他策略。

策略之间不共享业务后处理函数。上传、ComfyUI 队列提交、轮询、结果下载、缓存和鉴权属于后端基础设施，可以复用。`flux2` 和 `flux2_quant` 在 FLUX.2 第一阶段或 UPSCALE 二阶段失败时都直接返回失败，不回退到质量策略；插件收到失败后继续保留原图。

推理代码按以下边界组织：

```text
service/comic_enhancer/inference/
  contracts.py                 # 稳定推理契约
  factory.py                   # 后端组装
  routing.py                   # 原生档与主后端路由
  passthrough.py               # 开发后端
  realcugan.py                 # Real-CUGAN 放大实现
  comfyui/
    backend.py                 # 档位注册与分派
    transport.py               # 上传、队列、轮询与下载
    image_ops.py               # 几何恢复和结构保护
    workflows.py               # 完整工作流加载与版本计算
    strategies/
      fast.py
      quality.py
      flux2.py
      flux2_quant.py
      flux2_character.py
      anima_base.py
      anima_2_9b.py
```

八个 ComfyUI 档位各自实现可用性、缓存版本、适配器策略和处理逻辑。共享基类只定义窄契约，共享辅助只处理传输或无档位含义的算法；`ComfyUIBackend` 不再包含任何档位私有处理流程。FLUX.2 的二阶段放大由外层路由组合 UPSCALE 策略完成。Anima Base 与 Anima-2.9B 是独立实验档，工作流恢复原图尺寸后直出，不调用角色库或页面 VLM，也不进入 Real-CUGAN 二阶段。旧的 `backends.py`、`workflows.py` 和 `realcugan.py` 仅保留兼容导出，不承载业务实现。

`flux2_character` 是第五个独立 ComfyUI 档位。它通过 `character_vision` 窄接口访问独立 Qwen3-VL sidecar，并由 `character_library` 保存内容寻址参考图、SQLite 角色档案和轻量召回向量。Qwen3-VL 只在角色参考图首次进入角色库或档案版本变化时分析参考图；新档位处理漫画页时不调用页面 VLM，不生成页面 bbox 或角色 mask。Qwen 输出受控结构特征和采色区域，RGB 颜色由 Pillow 在参考图证据区域内确定性采样。工作流接收漫画原图、最多三张已建档角色参考图和静态 palette-only 提示，使用高质量 FLUX.2 四步空 latent 完成全页着色。默认路径保持 0.85MP、服务端几何恢复和原有二阶段契约；开启 `comfyui_flux2_character_native_resolution` 实验开关后，运行时按页面原图像素量调整漫画输入，使用工作流内 `ImageScale` 做最终原图尺寸校正，服务端不再执行原图 2x 的几何放大，再交给外层 Real-CUGAN 2x。`ComfyUI 原图直出` 仍只控制明度/墨线结构保护，不改变原图分辨率实验开关。两项开关只对 `flux2_character` 生效，不改变 Qwen3-VL 角色档案、参考图和提示词流程。

`flux2_character_lineart` 是独立的线稿保真档位。它复用 Qwen3-VL 角色库和三张参考图，但工作流、策略、缓存版本和模型标识均独立；FLUX.2 只提供色度，服务端固定保留原图明度、网点和墨线，禁止 `ComfyUI 原图直出`。首版不引入页面分格检测，先验证整页高密度线稿的结构稳定性。

`anima_base` 使用 `anima-base-v1.0`、Qwen3 0.6B、Qwen Image VAE 和 Anima LLLite Lineart。工作流把漫画页缩放到约 1MP，以 Lineart LLLite 作为唯一结构条件，使用 30 steps、CFG 4、`er_sde/simple` 空 latent 生成，再在工作流内恢复原图宽高并直出。`anima_2_9b` 使用 Anima-2.9B Preview v1 做 0.85MP 低降噪图生图（32 steps、CFG 4、Euler/`sgm_uniform`、denoise 0.35），同样不接受角色参考图，不执行服务端结构保护。两个档位只用于非商业实验，不能替代现有质量档的默认结论。

外部数据与本地存储能力也按职责分包：

```text
service/comic_enhancer/
  metadata/
    aggregator.py
    base.py
    providers/{bangumi,anilist,kitsu,shikimori,jikan,mangaupdates}.py
  identities/{models,matching,registry}.py
  references/{quality,store}.py
  storage/result_cache.py
```

元数据提供方之间不直接依赖，聚合器只面向 `MetadataProvider` 契约；参考图质量判断不执行网络请求，下载存储也不参与候选排序。原有包级导入继续由各目录的 `__init__.py` 提供，`cache.py` 只保留兼容导出。

服务端业务按领域、应用编排和 HTTP 接口继续分层：

```text
service/comic_enhancer/
  domain/{identity,processing,metadata}.py
  application/{processing,reference_bank}.py
  api/
    app.py
    context.py
    dependencies.py
    routes/{system,pages,metadata,results}.py
```

`domain` 只保存稳定数据契约；`application` 负责任务、角色参考库和远端适配器等用例编排；`api` 负责 FastAPI 对象装配、鉴权、请求校验和响应映射。路由之间不直接调用，统一通过 `ApplicationContext` 获取应用服务。`main.py`、`models.py` 和 `jobs.py` 只保留启动或旧导入路径兼容，不承载业务实现；`comic_enhancer.main:create_app`、`comic_enhancer.main:app` 和 `app.state.processor` 的外部契约保持不变。

## Real-CUGAN 放大档

放大档固定执行以下链路：

```text
原始漫画页
  -> EXIF 方向规范化并转换为临时 PNG
  -> 当前平台 resource/realcugan/<platform>/
  -> Real-CUGAN models-se，scale=2，noise=-1
  -> 校验输出宽高均为原图 2 倍
  -> 原子编码为 WebP 并写入统一结果缓存
  -> 返回 model_profile=realcugan-se-2x
```

缓存版本覆盖处理参数、可执行文件、`up2x-no-denoise.param` 和模型权重内容。平台资源目录支持 `windows-x64`、`windows-arm64`、`linux-x64`、`linux-arm64`、`macos-x64` 和 `macos-arm64`；仓库不提交任何平台包、动态库或模型权重。

当前 FLUX.2 最高质量档固定按以下顺序执行：

```text
原始漫画页
  -> 原图与最多 3 张角色参考图进入 FLUX.2 ReferenceLatent 条件
  -> 0.85MP FLUX.2 四步空 latent 上色，生成尺寸和采样参数保持不变
  -> 正向提示词锁定气泡、文字、标点、线条、网点和页面结构
  -> 不执行原图深色像素回注或颜色混合
  -> ComfyUI 工作流末端以 Lanczos 恢复为原图准确宽高
  -> API 校验首阶段输出为原图尺寸，不再执行 Pillow 二次插值
  -> UPSCALE 策略使用 Real-CUGAN 放大 2x
  -> 最终精确输出原图宽高各 2x
```

线稿保真档的附加链路为：

```text
原始漫画页 + 最多 3 张角色参考图
  -> 0.85MP FLUX.2 四步空 latent 生成颜色引导
  -> ComfyUI 恢复原图宽高
  -> 保留 FLUX.2 明度和色度，仅回注原图深色墨线
  -> Real-CUGAN 2x
  -> 最终精确输出原图宽高各 2x
```

三页授权基准确认旧四步直出的平滑动漫平涂、人物层次和背景完整性优于源图 latent 方案。全页深色像素回注会把原稿网点和颗粒重新覆盖到上色图，Anime 6B 与通用 Real-ESRGAN 也会把网点强化为伪纹理，因此这些处理都不进入 FLUX.2 正式输出链路。文字保护当前依赖强化提示词，已通过三页冒烟验证，但在完成至少 100 页准入前不能视为绝对像素级保证。

`flux2_character` 使用独立的 FLUX.2 四步空 latent 工作流，以漫画原图和三张已建档角色参考图构造 `ReferenceLatent` 条件，不增加页面级 mask 分支。角色档案中的完整调色板仅作为 palette-only 正向提示补充；不确定性限制只约束角色颜色复制，不能让背景、建筑、家具和物体保持灰阶。普通路径下工作流输出恢复原图几何后以固定 `1.80x` 增益保留全页生成色度，只回注原图明度、文字和深色墨线；直出模式跳过这些代码处理，仍由外层本地 Real-CUGAN 执行二阶段放大。启用原图分辨率实验开关后，漫画输入按原图像素量生成，工作流内完成宽高校正，服务端只在非直出模式执行结构保护，Real-CUGAN 直接从接近原图尺寸的阶段结果进行 2x 放大。该增益、直出状态、原图分辨率状态和缓存版本只属于角色档，不影响其他策略。新档位的页面缓存键仍包含原图哈希，但角色上下文键只包含作品、角色参考图和模型版本，因此同一作品的角色档案可跨页复用。

## 作品身份

作品主键固定为 `source:source_work_id`，例如 `copy_manga:12345`。标题、作者、标签和封面只是元数据，不作为主键，因为翻译标题、别名和同名作品都可能变化。

漫画站通常不提供 AniList/Bangumi ID。服务端使用 `config/work-identities.json` 保存已确认作品的长标题别名和外部 ID；只在唯一长别名完整匹配时补全 `external_ids`，页面明确传入的 ID 优先。映射不改变站点作品主键。

作品配置还可以登记跨站角色 ID 和名称别名。服务会把同一角色的 Bangumi/AniList 图片合并，只保留尺寸可用且包含有效颜色的候选，再按已确认作品来源、有效色彩、完整人物构图、分辨率、饱和度、细节和来源优先级选择角色参考图。只有灰度图的角色暂不进入参考库。

## 外部作品元数据

服务端提供 `POST /v1/metadata/resolve`，接收与页面处理相同的 `WorkIdentity`，按以下顺序查询公开数据源：

1. Bangumi：优先级最高，提供中文条目、封面、简介和角色介绍/角色图外链。
2. AniList：提供多语言标题、封面、简介、作者和角色档案。
3. Kitsu：提供标题、封面和作品简介。
4. Shikimori：提供俄文/日文标题、封面及可用简介。
5. Jikan/MAL：作为 MyAnimeList 的可选代理适配器。
6. MangaUpdates：保留可插拔适配器；只有配置自有 API 代理时才启用。

提供方按标题搜索时会结合作者和标题相似度计算置信度；外部 ID（`external_ids`）存在时优先按 ID 查询。结果缓存到 `runtime/metadata`，缓存只保存元数据和第三方外链，不镜像或重新发布第三方图片。处理页面时只读取已有缓存，缓存未命中会后台刷新，不阻塞当前秒级推理。

角色库优先使用 `external_ids` 精确命中的数据源，再考虑标题搜索候选；只有置信度不低于 `0.6` 的候选才可进入参考库。

## 推理工作流

首版使用远端已有节点可以直接运行的链路：

```text
输入漫画页
  -> 按模式缩放至 0.55MP 或 0.85MP
  -> SD1.5 动漫 checkpoint
  -> Lineart ControlNet 约束线稿结构
  -> 低降噪图生图 10 或 20 步
  -> Real-ESRGAN Anime 6B 4x
  -> 回缩至约 2x 并轻量锐化
  -> 以 Color 混合模式回注原图明度，并用原图深色像素蒙版覆盖文字、抗锯齿边缘、网点和墨线
  -> WebP 缓存
```

工作流使用 ComfyUI API 格式 JSON，checkpoint、ControlNet、提示词、随机种子、分辨率、采样器、步数、CFG、降噪和超分参数全部写在工作流内。服务只自动查找唯一的 `LoadImage` 节点写入上传结果，并查找 `SaveImage` 节点设置输出前缀、从 `/history` 获取结果。接入其他模型只需新增完整工作流并修改配置；工作流存在 `${...}` 占位符会被拒绝。

## 并发和预推理

- GPU 推理默认仅允许 1 个活动任务。
- 图片下载、哈希、编码可以与 GPU 推理重叠。
- 当前可视页优先级最高，之后按页面顺序处理预取队列。
- 插件只保留当前页前 2 页和后续至少 3 页的增强覆盖层；滚远的 Base64 结果从 DOM 释放，回看时从服务端不可变缓存恢复，避免长章节内存随页数持续增长。
- ComfyUI 命令队列、显存余量和 GPU 并发是三个独立概念，不能因为显存还有余量就自动增加并发。
- 缓存键包含原图哈希、作品 ID、处理模式、调色板版本、工作流版本和模型处理版本。

## 安全边界

- 处理接口、能力接口和结果接口都要求 Bearer Token。
- 远端 API 只部署在可信局域网，不直接暴露公网。
- 结果文件使用 SHA-256 文件名，且由插件后台鉴权拉取。

## 关键日志格式

角色视觉分析、角色库、工作流加载与提交、输入上传、任务轮询、ComfyUI 输出、服务端二次处理、两阶段推理和最终页面处理统一使用以下结构：

```text
功能=<功能名> 参数=<安全JSON> 结果=<安全JSON> 耗时_ms=<关键功能耗时>
```

非耗时型状态检查可以省略 `耗时_ms`。除显式的 `ComfyUI最终提示词` 记录外，日志参数和结果只记录作品键、档位、数量、版本及摘要前缀；`Token`、`Authorization`、API key 和图片字节必须自动脱敏，不得写入日志。

ComfyUI 运行日志会额外记录工作流文件路径、模型档位、工作流版本前缀、节点数量/类型/标题摘要、模型文件、尺寸、steps、CFG、sampler、seed 等关键运行参数、输入角色与图片尺寸、上传去重数量、队列 `prompt_id`、轮询状态、输出节点和结果尺寸；不会记录完整工作流 JSON。工作流完成动态绑定后，`ComfyUI最终提示词` 会按 `CLIPTextEncode` 节点完整记录节点 ID、标题和最终文本，包括角色调色板追加内容。FLUX.2 日志区分首阶段 ComfyUI 输出、角色档服务端后处理和 Real-CUGAN 二阶段，直出模式明确记录后处理为 `skipped`，以便定位颜色或尺寸问题发生在哪一阶段。

`POST /v1/pages/process` 会在鉴权和输入校验后记录安全化的入口参数，包括作品身份、处理选项、上传文件名/类型/字节数；成功出口记录与 HTTP 响应一致的 `ProcessResult`，失败出口记录状态码、阶段和错误类型。接口日志不记录 Bearer Token、请求图片内容或原始 multipart 请求体。

## Mac 与 AMD 边界

macOS 上的 RX 6750 XT 可以由原生 Metal 应用使用，PyTorch 是否可用取决于当前版本、算子覆盖和该 Hackintosh 的稳定性。Docker Desktop 容器不能取得 macOS Metal 设备，因此：

- Mac 原生 Python + MPS：可单独做实验。
- Mac Docker + ROCm：不可行。
- 原生 Linux + RX 6750 XT + `/dev/kfd`：可以使用本项目 AMD Compose 尝试。
- 本次交付：使用 `192.168.38.226` 的 RTX 4090。
