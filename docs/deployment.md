# 部署说明

## 本次 RTX 4090 部署

目标主机为 `holopix@192.168.38.226`。对外业务只提供漫画增强 API `8765`。项目统一 ComfyUI 容器在 Docker 网络中使用 `comfyui:8188`，宿主机 `192.168.38.226:8192` 仅提供工作流调试界面；快速、质量和 FLUX.2 都提交到该容器。主机已有的其他 ComfyUI 不会被替换。

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

API 容器只配置 `COMIC_ENHANCER_COMFYUI_URL=http://comfyui:8188`，不挂载 ComfyUI 模型目录。4090 主机统一使用 `/data1/models/ComfyUI/models` 作为模型根目录，并只挂载到统一 ComfyUI 的 `/root/sd/ComfyUI/models`。ComfyUI 的 checkpoint、ControlNet 和放大模型都不打进 API 镜像；Real-CUGAN 平台包也不打进镜像，只能从显式挂载的资源目录读取。

### Real-CUGAN 放大档

平台资源统一放在 `resource/realcugan/<platform>/`。Windows x64 本地服务使用：

```text
resource/realcugan/windows-x64/
  realcugan-ncnn-vulkan.exe
  vcomp140.dll
  models-se/up2x-no-denoise.param
  models-se/up2x-no-denoise.bin
```

在 `config/settings.json` 中显式开启：

```json
{
  "realcugan_enabled": true,
  "realcugan_resource_root": "resource/realcugan",
  "realcugan_timeout_seconds": 180
}
```

Docker API 容器运行 Linux，因此不能使用 `windows-x64` 包。远端必须另外准备 `linux-x64` 包，为 `realcugan-ncnn-vulkan` 添加执行权限，并通过 `.env` 的 `REALCUGAN_RESOURCE_ROOT` 挂载到容器；同时设置 `COMIC_ENHANCER_REALCUGAN_ENABLED=true`。能力接口只在当前平台的可执行文件和两倍无降噪模型齐全时返回 `upscale`。平台包、权重和样例输出不会提交到仓库，部署前必须独立审核其许可证。

### FLUX.2 Klein 最高质量模型

候选模型先在本机通过国内镜像断点下载，再上传到 4090 主机。下载脚本对新增大权重使用 Hugging Face LFS/Xet 公布的 SHA-256 校验：

```bash
CANDIDATE_DOWNLOAD_CONNECTIONS=4 \
  ./scripts/download_candidate_models_local.sh

./scripts/upload_candidate_models.sh
```

默认镜像为 `https://hf-mirror.com`，可通过 `CANDIDATE_HF_BASE_URL` 覆盖。只有全部新增权重和配置完成后才生成本机 `runtime/model-downloads/CandidateModels.ready`。上传脚本逐项比较远端哈希，一致文件直接跳过，不一致文件先续传到 `.uploading`，复验成功后原子改名。远端已有 `vae/flux2-vae.safetensors` 且所有检查通过后，才生成 `/data1/models/ComfyUI/models/candidate-models.ready`。

远端目录映射如下：

- FLUX.2 Klein 4B FP8：`diffusion_models/flux-2-klein-4b-fp8.safetensors`
- Qwen3 4B 编码器：`text_encoders/qwen_3_4b.safetensors`

ComfyUI 调试地址为 `http://192.168.38.226:8192/`，API 内部地址始终是 `http://comfyui:8188`。要启用最高质量档 FLUX.2 Klein 4B，设置 `COMIC_ENHANCER_COMFYUI_FLUX2_ENABLED=true`；要启用量化实验档，同时设置 `COMIC_ENHANCER_COMFYUI_FLUX2_QUANT_ENABLED=true`。两个档位都由增强 API 处理参考图，任一 FLUX.2 或 Real-CUGAN 阶段失败都会直接返回失败，不回退质量档。ComfyUI 调试界面没有业务鉴权，只应在可信局域网使用；插件仍必须走 `8765` API。

FLUX.2 最高质量档保持旧基准的 `0.85MP` 四步空 latent 生成参数，以强化提示词锁定气泡、文字、标点、页面结构和网点。工作流不执行全页深色像素回注或颜色混合；在 VAE 解码后使用工作流内 Lanczos 节点恢复到原图准确宽高，API 只校验尺寸，不再使用 Pillow 放大，再交给 UPSCALE 策略使用 Real-CUGAN 放大 2 倍，最终输出原图宽高各 2 倍。三页冒烟中该方案恢复了旧版平滑动漫平涂效果，并完整保留测试页文字；至少 100 页准入完成前仍需保留文字变化风险说明。

插件配置：

```text
运行方案: 快速模式/质量模式/放大模式（Real-CUGAN 2x）/最高质量模式（FLUX.2）/质量模式（FLUX.2 量化实验）
API Token: 与远端 .env 中 COMIC_ENHANCER_TOKEN 相同
```

插件只配置漫画增强服务地址，不提供 ComfyUI、FLUX.2 或 Real-CUGAN 地址入口。服务端也只配置一个 `COMIC_ENHANCER_COMFYUI_URL`；Real-CUGAN 配置是 API 本地资源根目录，不是独立推理 URL。

### Qwen3-VL 角色稳定档

角色稳定档使用独立 AMD Windows 主机常驻 `llama-server`，Comic Enhancer API 通过内网 OpenAI 兼容接口访问。插件仍只连接 Comic Enhancer API，不能直接连接 Qwen3-VL。当前已验证组合为 RX 7700 XT 12GB、`llamacpp-rocm b1311 gfx110X`、Qwen3-VL-4B-Instruct Q8_0 和 F16 mmproj。

在 AMD 主机创建仅运行账户可读的 `runtime/qwen3-vl-sidecar/api-key.txt`，写入至少 32 字节随机值，然后以前台服务方式启动：

```powershell
pwsh -File scripts/start_qwen3_vl_sidecar.ps1 `
  -HostAddress 0.0.0.0 `
  -Port 8080
```

脚本固定使用 `-ngl 99`、`-c 8192`、`--parallel 1`、`--image-min-tokens 1024`、`--jinja`、`--offline`、`--no-slots` 和 `--api-key-file`。Windows 防火墙只允许 Comic Enhancer API 主机访问 TCP 8080，不应把 sidecar 暴露到公网。

API 主机 `.env` 使用与 key 文件相同的值，并显式启用新档位：

```text
COMIC_ENHANCER_COMFYUI_FLUX2_CHARACTER_ENABLED=true
COMIC_ENHANCER_WORKFLOW_FLUX2_CHARACTER=/app/workflows/flux2-klein-4b-qwen3-vl-character-colorize.json
COMIC_ENHANCER_COMFYUI_FLUX2_CHARACTER_NATIVE_RESOLUTION=false
COMIC_ENHANCER_QWEN_VL_URL=http://<AMD主机内网IP>:8080
COMIC_ENHANCER_QWEN_VL_API_KEY=<sidecar key>
COMIC_ENHANCER_QWEN_VL_MODEL_ID=qwen3-vl-4b-instruct-q8_0
COMIC_ENHANCER_QWEN_VL_DEPLOYMENT_REVISION=q8_0-054721f4-mmproj-f16-256f3a43
```

开启 `COMIC_ENHANCER_COMFYUI_FLUX2_CHARACTER_NATIVE_RESOLUTION=true` 后，API 会按每页原图像素量调整角色工作流的漫画输入，并在 ComfyUI 内按原图宽高做尺寸校正，避免服务端先插值到原图 2x 再交给 Real-CUGAN。该实验路径会增加 FLUX.2 显存和首阶段耗时，必须先做单页显存与质量验收；关闭后恢复当前 0.85MP 基线。`ComfyUI 原图直出` 仍是独立的服务端结构保护开关。

能力接口只有在独立工作流存在、ComfyUI 可达、Qwen sidecar 健康、模型 ID 匹配且 Real-CUGAN 二阶段就绪时才返回 `flux2_character_available=true`。任何分析、JSON 校验、角色计划、FLUX.2 或放大阶段失败都直接让本档位失败，插件继续显示原图；不会回退到 `flux2`、`quality` 或其他档位。

替换其他 ComfyUI 工作流时，导出 API 格式 JSON，并在 `settings.json` 或环境变量中修改对应工作流路径。单输入工作流必须只有一个 `LoadImage`；多输入工作流使用 `_meta.title` 声明 `INPUT_IMAGE`、`REFERENCE_IMAGE` 等角色；所有工作流至少有一个 `SaveImage`，其余模型和参数必须全部预设。服务不依赖固定节点编号。

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
mkdir -p models/{checkpoints,controlnet,upscale_models}
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
