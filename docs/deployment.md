# 部署说明

## 本次 RTX 4090 部署

目标主机为 `holopix@192.168.38.226`。对外只提供漫画增强 API `8765`；已有 ComfyUI `8190`、MangaNinja 专用 ComfyUI `127.0.0.1:8191`、候选 Cobra ComfyUI `127.0.0.1:8192` 和 MAGIv2 分析器 `127.0.0.1:8770` 都是 API 的内部执行组件。专用容器与现有 8190 隔离，不重启或替换现有 ComfyUI，插件不配置这些地址。

远端需要确认：

```bash
nvidia-smi
curl http://127.0.0.1:8190/system_stats
curl http://127.0.0.1:8191/system_stats
curl http://127.0.0.1:8770/v1/health
docker compose version
```

部署：

```bash
cp .env.example .env
# 将 COMIC_ENHANCER_TOKEN 改为随机值
docker compose -f compose.nvidia-remote.yaml up -d --build
curl http://127.0.0.1:8765/v1/health
```

API 容器通过 `host.docker.internal:8190` 使用快速/质量 SD1.5 工作流。Compose 内网 `comfyui-manganinja:8188` 当前只供显式实验，不进入默认浏览器请求。4090 主机统一使用 `/data1/models/ComfyUI/models` 作为宿主机模型根目录：两个 ComfyUI 容器读取该目录，增强 API 将同一目录挂载为 `/models`，并只在 `/models/loras` 中自动下载和校验 LoRA。API 自身不占用 GPU 显存，checkpoint、ControlNet、放大模型、MangaNinja 权重和 LoRA 都不打进镜像。

MangaNinja 五个权重放在 `/data1/models/ComfyUI/models/MangaNinjia`，下载脚本支持分片续传，并在结束时按 Hugging Face 官方 LFS SHA-256 校验。节点源码固定在提交 `ab10b8a4e4628d3d778edf115a2ae8fbbe5817d4`，构建时通过镜像下载固定提交 tarball并校验 SHA-256。

MangaNinja 上游使用 `CC BY-NC 4.0`，当前部署只适用于已确认的非商用场景。模型权重只放在自有推理主机，不进入本项目 Git 仓库、Gitee LoRA 仓库或 Docker 镜像；若未来改变用途，必须先重新完成许可证评估。

MAGIv2 固定版本为 `ragavsachdeva/magiv2@fbc890fec52977142e8ee00bfe26e9458b65517c`，模型放在 `/data1/models/ComfyUI/models/magiv2`。其模型卡限定个人、研究、非商用、非营利用途，不能据此推导出商用或任意再分发权利。权重同样不进入 Git、Gitee 或镜像。热分析三张测试页约 `0.64` 秒，模型常驻约 `2.2-3.3 GiB` 显存，冷启动约 90 秒。

五个权重全部校验通过后，下载脚本才会生成 `MangaNinjia.ready`。专用容器的健康检查和增强 API 都检查该标记；下载未完成、哈希不一致或 8191 不可达时，MangaNinja 档请求回退至 8190 的质量工作流，不会进入一个必然失败的 MangaNinja 队列。本机下载可运行 `scripts/download_manganinja_local.sh`，默认从 `hf-mirror.com` 并行断点续传全部五项权重至 `runtime/model-downloads/MangaNinjia`；也可用 `MANGANINJA_HF_BASE_URL` 切换其他兼容国内镜像。完成后运行 `scripts/upload_manganinja_weights.sh`，五个文件均以 `.uploading` 名称续传并逐个原子改名，远端五项哈希全部通过后才生成正式就绪标记。

### Cobra 与 FLUX.2 Klein 候选模型

候选模型先在本机通过国内镜像断点下载，再上传到 4090 主机。本次新增下载约 19.7GB；Cobra 需要的 PixArt T5-XXL 直接复用 4090 主机已有的 `clip/t5xxl_fp16.safetensors`，避免重复下载约 19.05GB 的 FP32 分片。该文件与 PixArt 官方权重转为 FP16 后的代表性张量哈希一致，且 Cobra 上游固定以 FP16 加载 Pipeline。下载脚本固定 Cobra 与 PixArt 提交，并对 12 个新增大权重使用 Hugging Face LFS/Xet 公布的 SHA-256 校验：

```bash
CANDIDATE_DOWNLOAD_CONNECTIONS=4 \
  ./scripts/download_candidate_models_local.sh

./scripts/upload_candidate_models.sh
```

默认镜像为 `https://hf-mirror.com`，可通过 `CANDIDATE_HF_BASE_URL` 覆盖。只有全部新增权重和配置完成后才生成本机 `runtime/model-downloads/CandidateModels.ready`。上传脚本逐项比较远端哈希，一致文件直接跳过，不一致文件先续传到 `.uploading`，复验成功后原子改名；全部通过后还会严格校验远端 FP16 T5 的大小和 SHA-256，并在 PixArt `text_encoder` 目录建立容器内可解析的相对软链接。远端已有 `vae/flux2-vae.safetensors` 且所有检查通过后，才生成 `/data1/models/ComfyUI/models/candidate-models.ready`。

远端目录映射如下：

- FLUX.2 Klein 4B FP8：`diffusion_models/flux-2-klein-4b-fp8.safetensors`
- Qwen3 4B 编码器：`text_encoders/qwen_3_4b.safetensors`
- Cobra 项目权重：`cobra/JunhaoZhuang-Cobra`
- Cobra PixArt 基座：`cobra/PixArt-XL-2-1024-MS`
- Cobra 复用 T5：`cobra/PixArt-XL-2-1024-MS/text_encoder/model.safetensors -> ../../../clip/t5xxl_fp16.safetensors`

Cobra 通过 Compose 的 `candidate-cobra` profile 运行独立的 ComfyUI 容器，监听宿主机 `127.0.0.1:8192`，内部服务名为 `comfyui-cobra:8188`。该容器加载 Cobra 自定义节点和预设工作流，API 不直接调用 Cobra Python HTTP 服务。候选验收期间应先释放 MangaNinja 和 MAGIv2 的常驻显存，再执行 Cobra 工作流的冷启动、热模型和同页参考图基准；没有真实输出和显存峰值证据前，不把候选加入浏览器插件的正式档位。要在插件中试用 Cobra，API 容器设置 `COMIC_ENHANCER_COMFYUI_COBRA_URL=http://comfyui-cobra:8188`、`COMIC_ENHANCER_COMFYUI_COBRA_ENABLED=true` 后，以 `--profile candidate-cobra` 重建 API 和 ComfyUI；插件只会在该 ComfyUI `/system_stats` 就绪且工作流存在时显示该档位。

未配置 Gitee 仓库和 Token 时保持 `COMIC_ENHANCER_GITEE_ENABLED=false`，基础上色和本地 LoRA 仍可使用。填写完整 Gitee 配置后再改为 `true` 并重建 API 容器。

插件配置：

```text
运行方案: 远端增强服务 · 快速/质量/MangaNinja
API Token: 与远端 .env 中 COMIC_ENHANCER_TOKEN 相同
```

部署 MangaNinja 档时设置 `COMIC_ENHANCER_COMFYUI_REFERENCE_ENABLED=true` 和 `COMIC_ENHANCER_ANALYZER_ENABLED=true`。这两个服务端总开关只控制第三档是否可用；选择“质量”始终走 8190 的 `sd15-colorize-quality.json` 及作品/通用 LoRA 回退，不会调用 MAGIv2 或 MangaNinja。插件不接触管理员 Token 或 Gitee Token。

插件只配置漫画增强服务地址，不提供 ComfyUI、MAGIv2 或 MangaNinja 地址入口。基础增强 ComfyUI 可部署在宿主机或另一台机器，通过 API 容器的 `COMIC_ENHANCER_COMFYUI_URL` 配置；参考工作流地址通过 `COMIC_ENHANCER_COMFYUI_REFERENCE_URL` 配置。插件无需感知任何内部推理后端地址。

真实基准中，MangaNinja 使用 GhostMix V2、25 步、节点线稿预处理、每个人物 4 对 PointNet 对应点和 SAM 掩码回注。实验档会先运行一次 8190 质量工作流作为整页底图，再覆盖可靠角色；参考步骤失败时直接使用该底图。RTX 4090 上旧链路单人物热推理约 8.6 秒、两个不同分格人物约 16.8 秒；增加质量底图后的同一双人物页实测约 21.7 秒，缓存命中为 0 毫秒，因此只能作为浏览器预推理的独立实验档，不满足快速模式秒级目标。插件以 8 页为人物分析窗口，并让 MAGIv2 分析与 MangaNinja 推理在同一页面队列中串行，避免两个 GPU 服务同时抢占显存；4090 上并存其他 GPU 服务时仍必须预留采样峰值。OOM 会由 API 回退到已生成的质量底图，不能通过降低匹配安全阈值或强制占用其他业务显存解决。

替换其他 ComfyUI 工作流时，导出 API 格式 JSON，并在 `settings.json` 或环境变量中修改对应工作流路径。单输入工作流必须只有一个 `LoadImage`；多输入工作流使用 `_meta.title` 声明 `INPUT_IMAGE`、`REFERENCE_IMAGE` 等角色；所有工作流至少有一个 `SaveImage`，其余模型、LoRA 和参数必须全部预设。服务不依赖固定节点编号。

独立验证工作流可使用 `scripts/benchmark_comfyui.py`，通过多个 `--input ROLE=PATH` 绑定图片。脚本默认不覆盖工作流内的模型或采样参数，会将每轮结果与耗时报告写到 Git 忽略的 `runtime/benchmarks`；需要绕过 ComfyUI 节点缓存测真实热推理时，显式传入 `--seed-step 1`。

## 原生 Linux AMD 部署

只支持安装了 ROCm 内核驱动并存在以下设备的原生 Linux：

```bash
test -e /dev/kfd
test -e /dev/dri/renderD128
rocminfo
```

准备模型目录后运行：

```bash
mkdir -p models/{checkpoints,controlnet,upscale_models,loras}
cp .env.example .env
docker compose -f compose.amd.yaml up -d --build
```

RX 6750 XT 属于 Navi 22，默认提供 `HSA_OVERRIDE_GFX_VERSION=10.3.0` 作为兼容参数，但是否需要该值取决于实际 ROCm 版本和驱动识别结果。必须以 `rocminfo`、PyTorch HIP 可用性和真实工作流结果为准。

`Dockerfile.amd` 会固定安装远端已验证版本的 WAS Node Suite，工作流用其 Color 混色恢复原图明度；`LoadImage`、深色像素蒙版和 `SaveImage` 使用 ComfyUI 内置节点。

必须放置与 NVIDIA 工作流同名的模型文件：

- `models/checkpoints/SD1.5/SD1.5_GhostMix_V2.0.safetensors`
- `models/controlnet/SD1.5/control_v11p_sd15_lineart_fp16.safetensors`
- `models/upscale_models/RealESRGAN_x4plus_anime_6B.pth`

## macOS 说明

不能在 macOS Docker Desktop 中使用 `compose.amd.yaml`。Docker Desktop 的 Linux VM 没有 macOS Metal API，也没有 ROCm 所需的 `/dev/kfd`。

若以后验证 Mac 本机 RX 6750 XT，只能建立独立的原生 Python/MPS 环境，并先检查：

```python
import torch
print(torch.backends.mps.is_built())
print(torch.backends.mps.is_available())
```

该路径需要单独做算子兼容和稳定性测试，不与本次 4090 部署混用。
