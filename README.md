# 漫画上色增强器

面向漫画网站的浏览器插件和推理服务。首版适配拷贝漫画，按当前可视页优先处理，并预推理后续页面。推理端支持 ComfyUI，每个处理档位使用独立、完整的预设工作流；任一步失败时插件继续显示原图。

项目代码使用 Apache-2.0。模型权重沿用各自许可证，不随本仓库自动获得再发布许可。

## 当前实现

- 从拷贝漫画页面提取稳定作品 ID、章节和图片列表。
- 当前页优先，按配置预处理后续 0 到 8 页。
- 拷贝漫画当前话按页码顺序预生成，完成后继续预生成紧邻下一话并写入服务缓存。
- 原图保留，上色图覆盖显示；失败时仍显示原图。
- API Token 鉴权、结果鉴权和内容哈希缓存。
- ComfyUI 上传、提交、轮询和结果下载。
- 工作流自包含全部模型和采样参数；服务自动发现 `LoadImage` / `SaveImage` 节点。
- 作品元数据聚合：支持 Bangumi、AniList、Kitsu、Shikimori、Jikan/MAL，MangaUpdates 保留可配置适配器；Bangumi 角色简介可通过 `/v1/metadata/resolve` 获取。
- 可配置作品长标题别名映射，为不提供外部 ID 的漫画站补全已确认 AniList/Bangumi 身份。
- 五个独立处理档位：快速、质量、Real-CUGAN 放大、FLUX.2 和 FLUX.2 量化实验档；普通质量档不会隐式进入其他档位。
- 独立放大档使用当前平台的 Real-CUGAN SE 执行两倍无降噪超分；上色档使用 SD1.5 或 FLUX.2。
- 插件显示当前选择档位、最近一次真实执行模型和角色参考是否实际生效。
- 扩展安装、更新、浏览器启动和漫画页加载完成时都会检查内容脚本注入；页面重复占位图按 URL 去重并优先真实可见图片。

## 本次部署方式

```text
Mac / Chrome 插件
        |
        | 局域网 HTTP + Bearer Token
        v
192.168.38.226:8765  漫画增强 API（唯一对外入口）
        |
        | 按处理档位选择执行器
        +----> 平台 Real-CUGAN（放大）
        |
        +----> 统一 ComfyUI 容器 8192（快速/质量/FLUX.2）
        |
        v
漫画增强 API：必要的尺寸恢复、缓存和鉴权返回
```

插件只连接 `8765`。上色档只维护一个 ComfyUI 地址；放大档调用 API 所在平台的本地 Real-CUGAN 资源，不增加对外推理地址。

统一 ComfyUI 调试界面提供在 `http://192.168.38.226:8192/`，仅用于检查快速、质量和 FLUX.2 工作流；正式插件请求仍然必须走 `8765` API。

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

```win
uv run uvicorn comic_enhancer.main:app --app-dir service --port 8765
```

也可以直接运行包内启动文件：

```bash
python service/comic_enhancer/main.py
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
4. 在插件中选择“快速模式”“质量模式”“放大模式（Real-CUGAN 2x）”或其他已启用档位，填写漫画增强服务地址。
5. 输入部署 Token，保存并连接，然后授予漫画图片域名读取权限。
6. 打开拷贝漫画章节页面。

插件的唯一入口是本项目漫画增强服务，不直接连接 ComfyUI、FLUX.2 或 Real-CUGAN。增强服务按档位选择完整处理链；`upscale` 调用平台原生 Real-CUGAN，其他档位提交内部 ComfyUI 工作流。FLUX.2 Klein 4B 当前工作流最多使用 3 张参考图，并作为当前最高质量档；其结果随后进入 Real-CUGAN 二阶段放大。`flux2` 和 `flux2_quant` 任一阶段失败都直接保留原图，不回退到质量档；独立放大档同样不进入任何上色回退。漫画增强服务地址可编辑，默认是 `http://192.168.38.226:8765`；内部推理后端迁移时只修改服务端部署配置，插件配置保持不变。

外部元数据层可提供作品和角色参考候选；显式 `external_ids` 精确命中的数据源优先。角色图片只缓存于运行目录，不会自动上传或重新发布。

插件后台携带 Token 获取结果，再以页面内数据地址显示，避免 HTTPS 漫画页直接加载局域网 HTTP 图片产生混合内容问题。

## 远端 NVIDIA 部署

详见 [docs/deployment.md](docs/deployment.md)。项目使用一个统一 ComfyUI 容器执行全部增强工作流，不替换主机已有的其他 ComfyUI：

```bash
cp .env.example .env
docker compose -f compose.nvidia-remote.yaml up -d --build
```

## 模型和性能

默认快速工作流为约 0.55MP、10 步采样；质量工作流为约 0.85MP、20 步采样。两种模式都输出约 2 倍增强图，并在输出前用原图明度及深色像素蒙版恢复中文、抗锯齿边缘、气泡边界、网点和墨线。缓存命中目标不超过 300ms；RTX 4090 的热模型单页目标为快速模式 P50 不超过 2.5 秒、P95 不超过 4 秒。

这是验收目标，不是尚未实测前的性能承诺。首次加载 checkpoint、ControlNet 或放大模型会明显慢于热模型；连续阅读依靠后续页预推理隐藏等待时间。

模型选择和基准测试见 [docs/model-roadmap.md](docs/model-roadmap.md)。
完整 API 冒烟与 100 页质量准入门禁见 [benchmarks/README.md](benchmarks/README.md)。
