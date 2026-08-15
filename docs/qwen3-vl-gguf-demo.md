# Qwen3-VL GGUF 本机演示

本演示使用独立 Python 3.12 虚拟环境，通过 `llamacpp-rocm` 的 Windows `gfx110X`
预编译包在 AMD Radeon RX 7700 XT 上运行 Qwen3-VL-4B-Instruct。Python 只负责启动服务、
构造多模态请求和清理进程；模型由 `llama-server.exe` 通过 ROCm/HIP 加载。

## 文件与版本

- llama.cpp/ROCm：`lemonade-sdk/llamacpp-rocm`，固定发布 `b1311`。
- GPU 构建：`llama-b1311-windows-rocm-gfx110X-x64.zip`。
- Python：3.12，虚拟环境位于 `runtime/qwen3-vl-demo/.venv`。
- 主模型：`E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF\Qwen3VL-4B-Instruct-Q8_0.gguf`。
- 视觉投影：`E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF\mmproj-Qwen3VL-4B-Instruct-F16.gguf`。

运行时、虚拟环境、下载包和日志全部位于 Git 忽略的 `runtime/`，不会进入提交。

## 初始化

```powershell
pwsh -NoLogo -NoProfile -File scripts/setup_qwen3_vl_demo.ps1
```

安装脚本会执行以下检查：

- 使用 `uv` 创建 Python 3.12 虚拟环境；
- 安装独立的 `httpx` 依赖；
- 下载并校验 `b1311 gfx110X` 压缩包；
- 检查两个 GGUF 文件的大小和 SHA-256；
- 确认运行时中存在 `llama-server.exe`。

需要使用下载代理时，可以通过环境变量传入完整压缩包地址：

```powershell
$env:LLAMACPP_ROCM_ARCHIVE_URL = "https://example-mirror/llama-b1311-windows-rocm-gfx110X-x64.zip"
pwsh -NoLogo -NoProfile -File scripts/setup_qwen3_vl_demo.ps1
```

Windows 还需要 Microsoft Visual C++ 2015-2022 x64 Runtime。如果当前账户不能执行系统级安装，
可以把三个 x64 DLL 作为 app-local 依赖复制到运行目录：

```powershell
pwsh -NoLogo -NoProfile -File scripts/setup_qwen3_vl_demo.ps1 `
  -VcRuntimeRoot "C:\path\to\vc-runtime-x64"
```

该目录必须包含 `MSVCP140.dll`、`VCRUNTIME140.dll` 和 `VCRUNTIME140_1.dll`。安装脚本会在完成后
调用 `llama-server --version`，提前发现缺失 DLL，而不是等到运行 demo 时才失败。

## 单图演示

```powershell
runtime\qwen3-vl-demo\.venv\Scripts\python.exe `
  scripts\demo_qwen3_vl_gguf.py `
  --image "test\1677919416360014.jpg.c1500x.webp"
```

首次验证模型摘要时增加：

```powershell
runtime\qwen3-vl-demo\.venv\Scripts\python.exe `
  scripts\demo_qwen3_vl_gguf.py `
  --image "test\1677919416360014.jpg.c1500x.webp" `
  --verify-model-hash
```

## 多参考图与 JSON

```powershell
runtime\qwen3-vl-demo\.venv\Scripts\python.exe `
  scripts\demo_qwen3_vl_gguf.py `
  --image "test\1677919416360014.jpg.c1500x.webp" `
  --reference "test\艾尔玛.jpg" `
  --reference-name "艾尔玛" `
  --reference "test\路切.jpg" `
  --reference-name "路切" `
  --json `
  --output "runtime\qwen3-vl-demo\result.json"
```

演示最多接受三张有序角色参考图。Python 会明确发送 `Picture 1 = current manga page` 和后续
候选角色名/槽位说明，并使用 Pillow 将 WebP 等输入规范化为 PNG；图片 Base64 不会写入日志。
默认提示只执行候选角色匹配与定位，不总结场景或剧情。未传 `--reference-name` 时使用参考图文件名；
模型返回候选列表外的角色名会被 Python 拒绝。

## 默认资源参数

```text
GPU layers: 99
Context: 8192
并发: 1
每张图片最低 tokens: 1024
Temperature: 0.0
最大输出: 512 tokens
监听地址: 127.0.0.1
```

`llama-server` 日志写入 `runtime/qwen3-vl-demo/logs/`。若模型加载失败，应先检查日志是否识别
`gfx1101`、是否把模型层卸载到 GPU，以及 `--mmproj` 是否被当前构建接受。

## 常见问题

- `llama-server.exe` 缺失：重新运行安装脚本，不能只复制单个 EXE，ROCm DLL 必须保留在解压目录。
- 缺少 DLL：确认使用 Windows `gfx110X` 包，并安装 Microsoft Visual C++ 2015-2022 x64 Runtime。
- 显存不足：降低 `--ctx-size`，减少参考图数量或缩小输入图片。
- 多模态参数不支持：该项目 CI 只验证纯文本模型，需要固定当前已通过真实图片冒烟的发布版本。
- 端口冲突：默认端口为自动选择；只有显式传入 `--port` 时才需要手工处理冲突。
