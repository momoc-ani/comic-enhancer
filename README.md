# 漫画上色增强器

面向漫画网站的浏览器插件和推理服务。首版适配拷贝漫画，按当前可视页优先处理，并预推理后续页面。推理端支持 ComfyUI，默认策略为：

1. 有当前作品的兼容 LoRA：使用作品级 LoRA。
2. 没有作品 LoRA：使用已安装且兼容的通用二次元 LoRA。
3. 两者都不可用：使用基础上色工作流，不阻塞阅读。

项目代码使用 Apache-2.0。模型和 LoRA 权重沿用各自许可证，不随本仓库自动获得再发布许可。

## 当前实现

- 从拷贝漫画页面提取稳定作品 ID、章节和图片列表。
- 当前页优先，按配置预处理后续 0 到 8 页。
- 原图保留，上色图覆盖显示；失败时仍显示原图。
- API Token 鉴权、结果鉴权和内容哈希缓存。
- 作品 LoRA、通用 LoRA、无 LoRA 的确定性回退。
- LoRA 基模兼容、文件存在性和 SHA-256 校验。
- ComfyUI 上传、提交、轮询和结果下载。
- SD1.5 + Lineart ControlNet 上色，以及 Real-ESRGAN Anime 6B 增强。

## 本次部署方式

```text
Mac / Chrome 插件
        |
        | 局域网 HTTP + Bearer Token
        v
192.168.38.226:8765  漫画增强 API 容器
        |
        | Docker 宿主机 8190
        v
已有 ComfyUI 容器 + RTX 4090
        |
        v
/data1/models/ComfyUI/models 统一模型挂载目录
```

本次不使用 Mac 的 RX 6750 XT 推理。macOS 原生程序可以尝试 PyTorch MPS/Metal，但 Docker Desktop 运行的是 Linux 虚拟机，无法直通 macOS AMD 显卡，也不能提供 ROCm 所需的 `/dev/kfd`。

`Dockerfile.amd` 和 `compose.amd.yaml` 仅供未来的原生 Linux + RX 6750 XT 使用，不适用于 macOS。

## 本地开发

```bash
uv sync --extra dev
cp config/settings.example.json config/settings.json
uv run uvicorn comic_enhancer.main:app \
  --app-dir service \
  --host 127.0.0.1 \
  --port 8765
```

开发环境默认使用 `passthrough` 后端，只验证插件、API、缓存和回退协议，不会伪装成真实 AI 上色。

```bash
curl http://127.0.0.1:8765/v1/health
uv run pytest
node --check extension/background.js
node --check extension/content.js
node --check extension/popup.js
```

## 安装 Chrome 插件

1. 打开 `chrome://extensions`。
2.启用“开发者模式”。
3. 选择“加载已解压的扩展程序”，选择 `extension/`。
4. 在插件中填写 `http://192.168.38.226:8765` 和部署 Token。
5. 保存配置并授予漫画图片域名读取权限。
6. 打开拷贝漫画章节页面。

插件后台携带 Token 获取结果，再以页面内数据地址显示，避免 HTTPS 漫画页直接加载局域网 HTTP 图片产生混合内容问题。

## 远端 NVIDIA 部署

详见 [docs/deployment.md](docs/deployment.md)。现有 4090 主机复用宿主机 `8190` 的 ComfyUI，不再启动第二个重型 GPU 容器：

```bash
cp .env.example .env
docker compose -f compose.nvidia-remote.yaml up -d --build
```

## 模型和性能

默认快速模式为约 0.55MP、8 步采样；高质量模式为约 0.85MP、12 步采样。两种模式都输出约 2 倍增强图。缓存命中目标不超过 300ms；RTX 4090 的热模型单页目标为快速模式 P50 不超过 2.5 秒、P95 不超过 4 秒。

这是验收目标，不是尚未实测前的性能承诺。首次加载 checkpoint、ControlNet 或放大模型会明显慢于热模型；连续阅读依靠后续页预推理隐藏等待时间。

模型选择、LoRA 发布和基准测试见 [docs/model-roadmap.md](docs/model-roadmap.md) 与 [docs/adapters.md](docs/adapters.md)。
