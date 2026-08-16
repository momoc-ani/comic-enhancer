# 项目协作说明


## 重要说明

- 先给出整体方案，重要决策经用户审查后再实现。
- 本项目的界面是 Chrome Manifest V3 原生扩展，使用现有 HTML、CSS 和 JavaScript 模式；
  不要擅自引入前端框架或组件库。
- 插件、API、ComfyUI 工作流和模型部署是同一条处理链，修改跨层契约时必须同步检查所有层。

## 语言约束

全程使用中文沟通。新增代码注释使用中文；协议字段、第三方 API 名称和无法准确翻译的技术名词
保留英文。

## 项目概览

Comic Enhancer 是一个本地优先的漫画上色与增强系统。Chrome 插件从受支持的漫画站提取作品、
章节和图片，按视口优先级调用唯一的 FastAPI 服务；服务负责鉴权、元数据聚合、适配器选择、
缓存和 ComfyUI 推理，失败时插件继续显示原图。

当前技术栈：

- Chrome Manifest V3 / 原生 JavaScript / HTML / CSS
- Python 3.11 / FastAPI / Pydantic / HTTPX / Pillow
- ComfyUI API 工作流 / Cobra 自定义节点 / FLUX.2 实验工作流
- Docker / Docker Compose / NVIDIA CUDA；AMD Compose 仅面向原生 Linux ROCm
- Gitee Release 适配器索引与权重分发
- uv / pytest / Node.js 原生测试运行器

插件只能访问 Comic Enhancer API，不能直接连接 ComfyUI。服务端只配置一个 ComfyUI 地址，
快速、质量、Cobra、FLUX.2 和量化实验档通过完整工作流与独立策略切换。

## 常用命令

```powershell
uv sync --extra dev
Copy-Item config/settings.example.json config/settings.json
uv run uvicorn comic_enhancer.main:app --app-dir service --host 127.0.0.1 --port 8765
uv run pytest

node --experimental-default-type=module --check extension/background.js
node --experimental-default-type=module --check extension/content.js
node --experimental-default-type=module --check extension/popup.js
node --experimental-default-type=module --check extension/settings.js
node --experimental-default-type=module --check extension/model-status.js
node --experimental-default-type=module --test extension/background.test.mjs extension/content.test.mjs extension/model-status.test.mjs extension/popup.test.mjs extension/settings.test.mjs
```

远端 NVIDIA 部署：

```powershell
Copy-Item .env.example .env
docker compose -f compose.nvidia-remote.yaml up -d --build
```

说明：

- `config/settings.json`、`.env`、Token、模型权重、`runtime/` 缓存和基准输出不得提交。
- 开发默认使用 `passthrough` 后端，只验证插件、API、缓存和回退协议，不代表真实上色质量。
- 工作流和模型相关改动除单元测试外，还要按 `benchmarks/README.md` 执行对应冒烟或准入基准。

## 关键目录

- `extension`：Chrome 插件后台、内容脚本、设置弹窗、样式和 Node.js 测试。
- `service/comic_enhancer`：FastAPI 应用、任务编排、推理后端、工作流、缓存、身份和元数据服务。
- `service/tests`：Python API、后端、工作流、元数据、引用图和结构保护测试。
- `workflows`：可直接提交给 ComfyUI 的完整 API 格式工作流。
- `comfyui_nodes/cobra_colorize`：Cobra ComfyUI 自定义节点和隔离工作进程。
- `adapters`：作品 LoRA 与通用 LoRA 的本地索引。
- `config`：服务设置示例和已确认的作品/角色身份映射。
- `scripts`：部署辅助、模型下载和基准测试工具。
- `benchmarks`：授权测试集清单格式、质量指标和准入规则。
- `docs`：架构、部署、模型路线、适配器和 LoRA 数据规范。

## 当前已实现

- 拷贝漫画作品、章节与图片识别，重复占位图去重，当前页优先和后续页预取。
- 原图保留、增强图覆盖、失败重试、设置变更重置和远距离结果释放。
- Bearer Token 鉴权、内容哈希缓存和不可变 WebP 结果返回。
- 作品 LoRA、通用 LoRA、无 LoRA 的确定性回退，以及基模、工作流、文件和 SHA-256 校验。
- 单一 ComfyUI 服务中的快速、质量、Cobra、FLUX.2 和 FLUX.2 量化实验策略。
- ComfyUI 上传、队列提交、轮询、结果下载、几何恢复和文字/墨线结构保护。
- Bangumi、AniList、Kitsu、Shikimori、Jikan/MAL 元数据聚合及可配置 MangaUpdates 适配器。
- 作品长标题别名、外部 ID 和跨提供方角色身份合并。
- Gitee 适配器索引同步、权重下载校验和显式发布流程。
- 完整 API 基准、候选模型对比和质量/资源准入门禁。

## 当前未完成

- 最终高质量模型路线仍需至少 100 页授权数据集的画质、性能和稳定性验收。
- SD1.5 当前质量档仍偏灰白，不能视为已经达到最终上色质量目标。
- 仓库不捆绑许可证未确认的通用 LoRA；候选权重必须逐项完成许可证和再分发审查。
- 原生 Linux + AMD ROCm 链路仍是未来部署选项，不适用于 macOS Docker Desktop。

## 开发约束

- 新增或修改处理档位时，同步更新后端策略、工作流加载器、能力接口、扩展设置、模型状态展示、
  缓存版本和测试。
- 每个处理档位独立实现可用性、缓存版本、适配器策略和处理逻辑；实验档失败只能显式回退质量档，
  返回的 `model_profile` 必须反映真实执行模型。
- 插件不得绕过 API 访问 ComfyUI、模型服务或未经鉴权的结果地址。
- 页面处理任一步失败都必须保留原图，运行时错误不得让漫画页或设置弹窗失去基本可用性。
- 工作流必须是自包含的 ComfyUI API 格式 JSON，不得保留 `${...}` 占位符；输入、参考图和输出节点
  必须能被加载器唯一、稳定地发现。
- 缓存键必须覆盖所有会影响结果的输入，包括原图、作品身份、处理档位、工作流/模型版本、调色板
  和实际适配器。
- 外部 ID 精确命中的元数据优先于标题搜索；网络元数据刷新不得阻塞当前页面的秒级推理。
- 第三方角色图只允许进入运行时缓存，不得自动提交、镜像或重新发布。
- 可下载适配器只接受 `safetensors`，下载地址、基模兼容性、文件存在性和 SHA-256 必须全部校验。
- 训练数据必须有明确使用权；发布作品 LoRA 必须由用户主动确认。
- 新增可替换能力时优先使用窄接口或独立服务，保持高内聚、低耦合；仅在确有复杂度收益时使用设计模式。
- 每个函数或方法都要有简短、明确的中文用途说明；不要逐行复述代码，也不要添加无信息量注释。
- 对启动关闭、外部请求、回退、缓存异常、模型切换和长任务等关键步骤记录必要日志；不得记录 Token
  或受保护内容。
- 不要提交生成物、运行时缓存、下载中的临时文件、模型权重或测试数据副本。
- 重要架构、模型、许可证、安全边界和发布决策必须由用户审核。
- 必须要要写注释，每个方法都要说明作用！

## 已知注意事项

- macOS Docker Desktop 无法把 AMD GPU 以 ROCm 所需的 `/dev/kfd` 形式传入 Linux 容器。
- 冷启动、热模型和缓存命中的耗时必须分别记录，不能混成同一性能结论。
- ComfyUI 队列长度、GPU 并发数和显存余量是不同概念；不能仅因显存尚有余量就提高并发。
- Cobra 与 FLUX.2 使用不同的参考图数量、显存释放和结构处理策略，不能隐式共用业务后处理。
- 工作流、模型参数或结构保护算法变化时必须更新缓存版本，防止旧结果被错误复用。
- 漫画站 DOM 和懒加载属性可能变化；站点适配器修改后要覆盖真实地址解析、图片去重和失败回退测试。

## 代码提交约束

- 新需求或功能提交以 `feat` 开头，并说明增加了什么能力。
- Bug 修复提交以 `fix` 开头，并说明问题、修复方式和必要的回归测试。
- 不要把无关格式化、生成物或本地配置混入提交。

## 其他文档约束，需要静默加载

根据任务范围读取对应文档，不需要向用户逐项汇报加载过程：

- `docs/architecture.md`
- `docs/deployment.md`
- `docs/adapters.md`
- `docs/model-roadmap.md`
- `docs/lora-training-input.md`
- `benchmarks/README.md`


## 日志打印规则
- 只需要关键点打印，不用打印太多的
- 打印格式，功能 + 参数 + 结果 + 【关键功能需要】耗时