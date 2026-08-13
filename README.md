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
- 工作流自包含全部模型和采样参数；服务自动发现 `LoadImage` / `SaveImage` 节点。
- 作品元数据聚合：支持 Bangumi、AniList、Kitsu、Shikimori、Jikan/MAL，MangaUpdates 保留可配置适配器；Bangumi 角色简介可通过 `/v1/metadata/resolve` 获取。
- MAGIv2 批量分格、人物检测、跨页保守聚类和角色拒绝机制；精确外部 ID 角色库优先。
- 可配置作品长标题别名映射，为不提供外部 ID 的漫画站补全已确认 AniList/Bangumi 身份。
- MangaNinja 分格多角色参考板与 PointNet 对应点，失败时自动回退主质量工作流。
- SD1.5 + Lineart ControlNet 上色、原始明度/墨线回注，以及 Real-ESRGAN Anime 6B 增强。
- 插件显示当前选择档位及最近一次真实执行模型，明确作品 LoRA、通用 LoRA、基础回退和角色参考是否实际生效。
- 扩展安装、更新、浏览器启动和漫画页加载完成时都会检查内容脚本注入；页面重复占位图按 URL 去重并优先真实可见图片。

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
4. 在插件中选择“远端增强服务 · 快速”或“远端增强服务 · 质量”，填写漫画增强 API 地址。
5. 输入部署 Token，保存并连接，然后授予漫画图片域名读取权限。
6. 打开拷贝漫画章节页面。

插件连接的是漫画增强 API，不直接连接 ComfyUI。快速/质量模式会选择对应完整工作流；作品 LoRA、通用 LoRA 和无 LoRA 的回退由 API 自动完成。远端增强 API 地址可编辑，默认是 `http://192.168.38.226:8765`；基础 ComfyUI 部署到其他机器时，只修改增强 API 服务端的 `COMIC_ENHANCER_COMFYUI_URL`。

外部元数据层可提供作品和角色参考候选；显式 `external_ids` 精确命中的数据源优先。质量模式可预分析最多 8 页，只为可靠绑定的分格生成角色参考板；歧义人物保持拒绝。生成后回注原图中文、网点和墨线。该参考工作流仍默认关闭，角色图片只缓存于运行目录，不会自动进入 Git/Gitee 或被重新发布。

插件后台携带 Token 获取结果，再以页面内数据地址显示，避免 HTTPS 漫画页直接加载局域网 HTTP 图片产生混合内容问题。

## 远端 NVIDIA 部署

详见 [docs/deployment.md](docs/deployment.md)。现有 4090 主机复用宿主机 `8190` 的 ComfyUI，不再启动第二个重型 GPU 容器：

```bash
cp .env.example .env
docker compose -f compose.nvidia-remote.yaml up -d --build
```

## 模型和性能

默认快速工作流为约 0.55MP、10 步采样；质量工作流为约 0.85MP、16 步采样。两种模式都输出约 2 倍增强图，并在输出前用原图明度及深色像素蒙版恢复中文、抗锯齿边缘、气泡边界、网点和墨线。缓存命中目标不超过 300ms；RTX 4090 的热模型单页目标为快速模式 P50 不超过 2.5 秒、P95 不超过 4 秒。

这是验收目标，不是尚未实测前的性能承诺。首次加载 checkpoint、ControlNet 或放大模型会明显慢于热模型；连续阅读依靠后续页预推理隐藏等待时间。

模型选择、LoRA 发布和基准测试见 [docs/model-roadmap.md](docs/model-roadmap.md) 与 [docs/adapters.md](docs/adapters.md)。
完整 API 冒烟与 100 页质量准入门禁见 [benchmarks/README.md](benchmarks/README.md)。
