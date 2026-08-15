# 部署说明

## 本次 RTX 4090 部署

目标主机为 `holopix@192.168.38.226`。对外业务只提供漫画增强 API `8765`。项目统一 ComfyUI 容器在 Docker 网络中使用 `comfyui:8188`，宿主机 `192.168.38.226:8192` 仅提供工作流调试界面；快速、质量、Cobra 和 FLUX.2 都提交到该容器。主机已有的其他 ComfyUI 不会被替换。

远端需要确认：

```bash
nvidia-smi
curl http://192.168.38.226:8192/system_stats
docker compose version
```

部署：

```bash
cp .env.example .env
# 将 COMIC_ENHANCER_TOKEN 改为随机值
docker compose -f compose.nvidia-remote.yaml up -d --build
curl http://127.0.0.1:8765/v1/health
```

API 容器只配置 `COMIC_ENHANCER_COMFYUI_URL=http://comfyui:8188`。4090 主机统一使用 `/data1/models/ComfyUI/models` 作为模型根目录：统一 ComfyUI 挂载为 `/root/sd/ComfyUI/models`，增强 API 挂载为 `/models`，并只在 `/models/loras` 中自动下载和校验 LoRA。API 自身不加载生成模型；checkpoint、ControlNet、放大模型、Cobra 和 LoRA 权重都不打进镜像。

### Cobra 候选与 FLUX.2 Klein 最高质量模型

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

Cobra 节点安装在统一 ComfyUI 镜像中，调试地址为 `http://192.168.38.226:8192/`，API 内部地址始终是 `http://comfyui:8188`。API 不调用 Cobra Python HTTP 服务，也不存在 `cobra_url:8780`。Cobra 的旧 Diffusers 运行时隔离在同容器的 `/opt/cobra-venv`，ComfyUI 节点通过 Unix Socket 调用常驻 worker，因此不会覆盖主环境中 FLUX.2 所需的新版本依赖，也不会新增网络端口。要启用 Cobra，设置 `COMIC_ENHANCER_COMFYUI_COBRA_ENABLED=true`；要启用当前最高质量档 FLUX.2 Klein 4B，设置 `COMIC_ENHANCER_COMFYUI_FLUX2_ENABLED=true`。两个档位都由增强 API 处理参考图和质量回退。ComfyUI 调试界面没有业务鉴权，只应在可信局域网使用；插件仍必须走 `8765` API。

FLUX.2 最高质量档恢复旧基准的 `0.85MP` 四步空 latent 直出，以强化提示词锁定气泡、文字、标点、页面结构和网点。工作流不执行全页深色像素回注、颜色混合或神经超分；API 只将未后处理结果按原图比例输出为宽高各 2 倍。三页冒烟中该方案恢复了旧版平滑动漫平涂效果，并完整保留测试页文字；至少 100 页准入完成前仍需保留文字变化风险说明。

未配置 Gitee 仓库和 Token 时保持 `COMIC_ENHANCER_GITEE_ENABLED=false`，基础上色和本地 LoRA 仍可使用。填写完整 Gitee 配置后再改为 `true` 并重建 API 容器。

插件配置：

```text
运行方案: 快速模式/质量模式/Cobra/最高质量模式（FLUX.2）
API Token: 与远端 .env 中 COMIC_ENHANCER_TOKEN 相同
```

插件只配置漫画增强服务地址，不提供 ComfyUI、Cobra 或 FLUX.2 地址入口。服务端也只配置一个 `COMIC_ENHANCER_COMFYUI_URL`，不同档位通过工作流路由，不允许再配置独立推理 URL。

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
