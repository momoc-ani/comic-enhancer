# 部署说明

## 本次 RTX 4090 部署

目标主机为 `holopix@192.168.38.226`。已有 ComfyUI 容器监听宿主机 `8190`，本项目只增加一个轻量 API 容器监听 `8765`。

远端需要确认：

```bash
nvidia-smi
curl http://127.0.0.1:8190/system_stats
docker compose version
```

部署：

```bash
cp .env.example .env
# 将 COMIC_ENHANCER_TOKEN 改为随机值
docker compose -f compose.nvidia-remote.yaml up -d --build
curl http://127.0.0.1:8765/v1/health
```

API 容器通过 `host.docker.internal:8190` 复用 ComfyUI。4090 主机统一使用 `/data1/models/ComfyUI/models` 作为宿主机模型根目录：现有 ComfyUI 容器读取该目录，增强 API 将同一目录挂载为 `/models`，并只在 `/models/loras` 中自动下载和校验 LoRA。API 自身不占用 GPU 显存，checkpoint、ControlNet、放大模型和 LoRA 都不打进镜像。

插件配置：

```text
推理服务: http://192.168.38.226:8765
API Token: 与远端 .env 相同
模式: 快速
预处理页数: 3
```

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
