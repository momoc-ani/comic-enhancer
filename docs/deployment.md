# 部署说明

## 本次 RTX 4090 部署

目标主机为 `holopix@192.168.38.226`。已有 ComfyUI 容器监听宿主机 `8190`，本项目增加一个 MangaNinja 专用 ComfyUI 容器监听 `8191`，以及一个轻量 API 容器监听 `8765`。专用容器与现有 8190 隔离，不重启或替换现有 ComfyUI。

远端需要确认：

```bash
nvidia-smi
curl http://127.0.0.1:8190/system_stats
curl http://127.0.0.1:8191/system_stats
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

五个权重全部校验通过后，下载脚本才会生成 `MangaNinjia.ready`。专用容器的健康检查和增强 API 都检查该标记；下载未完成、哈希不一致或 8191 不可达时，质量请求回退至 8190 的基础质量工作流，不会进入一个必然失败的 MangaNinja 队列。本机下载可运行 `scripts/download_manganinja_local.sh`，默认从 `hf-mirror.com` 并行断点续传全部五项权重至 `runtime/model-downloads/MangaNinjia`；也可用 `MANGANINJA_HF_BASE_URL` 切换其他兼容国内镜像。完成后运行 `scripts/upload_manganinja_weights.sh`，五个文件均以 `.uploading` 名称续传并逐个原子改名，远端五项哈希全部通过后才生成正式就绪标记。

未配置 Gitee 仓库和 Token 时保持 `COMIC_ENHANCER_GITEE_ENABLED=false`，基础上色和本地 LoRA 仍可使用。填写完整 Gitee 配置后再改为 `true` 并重建 API 容器。

插件配置：

```text
运行方案: 远端 RTX 4090 · 快速
API Token: 与远端 .env 中 COMIC_ENHANCER_TOKEN 相同
```

首版保持 `COMIC_ENHANCER_COMFYUI_REFERENCE_ENABLED=false`：选择“质量”时走 8190 的 `sd15-colorize-quality.json` 及作品/通用 LoRA 回退。8191 当前只用于 MangaNinja 单角色、单格和后续分格实验；未完成分格质量验收前不得把该开关设为 `true`。插件不接触管理员 Token 或 Gitee Token。

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
