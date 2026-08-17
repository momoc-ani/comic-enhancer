# 基准测试

## 当前阶段

`works/exiled-reincarnated-heavy-knight.json` 登记了用户提供的 3 页本地非商用冒烟样本。它用于验证完整 API、真实模型路由、输出尺寸和墨线保留，不能替代至少 100 页授权测试集，也不能把样本或生成图提交、上传或重新分发。

Anima Base 和 Anima-2.9B 曾作为非商业实验候选进行本地基准，但已因重绘、灰度和文字漂移问题移除实现；相关历史数据只用于解释淘汰原因，不再提供可运行档位。

2026-08-16 的 RTX 4090 单页冒烟中，`anima_base` HTTP 200、约 14.1 秒、输出 `1124x1600`，但人物、场景和文字均被明显重绘；`anima_2_9b` 安装官方 loader patch 后 HTTP 200、约 15.5 秒且不再输出空白，正式 `denoise=0.35` 结果仍基本灰度并改写文字。`denoise=0.45/0.55/0.65` 的临时扫描不能采用：颜色增强与结构漂移同时上升。Base 的 28 层 LLLite 权重不能加载到 2.9B 的 40 层结构。两档链路可运行，但均未通过“只上色、不重绘”质量门禁。

追加扫描记录曾保存在本地 `.dev` 目录。2.9B 的 `denoise=0.35~0.45、CFG=3~4` 饱和色覆盖最高约 `0.75%`，暗线保留约 `66%~72%`；降低到 `denoise=0.25~0.35、CFG=1.5~2` 虽将暗线保留提高到约 `78.5%`，但饱和色覆盖为 `0%`。Base 的 LLLite 强度 `0.8~1.4` 饱和色覆盖约 `31%~60%`，暗线保留低于 `1%`。扫描未找到满足两项质量要求的参数窗口，因此两个实现已删除。

同日质量工作流 A/B 采用相同三页比较保守、均衡和鲜艳参数。鲜艳候选在直接 ComfyUI 测试中将饱和色覆盖中位数从 `6.63%` 提升到 `11.92%`、有效色相中位数从 4 提升到 5，主导色中位数保持约 `44.66%`，P50 从 `4.129s` 增加到 `4.392s`；完整 API 的结构保护后 3/3 成功，P50 `5.416s`、P95 `5.549s`，深色墨线最低保留 `100%`、中深色结构最低 `99.94%`、白底最低 `99.80%`、有效色相中位数 5、最差页主导色占比 `51.95%`。不过完整 API 的饱和色覆盖中位数仅 `3.06%`，人工检查仍然偏灰和偏淡，因此只记录为冒烟改善，不构成画质准入。

运行完整 API 基准时从环境变量读取推理 Token，避免 Token 出现在命令参数、报告或进程列表：

```bash
COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode fast

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode quality

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://127.0.0.1:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode upscale --phase warm \
  --palette-version realcugan-se-2x-smoke-v1

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode flux2 --phase warm \
  --resource-ssh-host holopix@192.168.38.226

# 两个默认关闭的 FLUX.2 验收候选启用后，可分别使用以下 mode。
COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode flux2_9b_lora --phase warm \
  --resource-ssh-host holopix@192.168.38.226

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode flux2_9b_fast --phase warm \
  --resource-ssh-host holopix@192.168.38.226

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode flux2_9b_fast_lowres --phase warm \
  --resource-ssh-host holopix@192.168.38.226

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode flux2_4b_source --phase warm \
  --resource-ssh-host holopix@192.168.38.226

COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py \
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode flux2_4b_color --phase warm \
  --resource-ssh-host holopix@192.168.38.226
```

结果写入 `runtime/benchmarks/api/`，包括每页输出和 `report.json`。报告不包含 Token。`phase` 必须显式区分冷启动、热模型和缓存，脚本不会为了制造冷启动数据而擅自重启模型服务：

```bash
# 运维人员确认模型已经卸载或服务刚启动后，才能标为 cold。
COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py ... --phase cold

# 热推理使用唯一 palette_version 绕过结果缓存。
COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py ... \
  --phase warm --palette-version admission-warm-v1

# 先用同一个 palette_version 完成一轮预热，再运行 cache 报告。
COMIC_ENHANCER_TOKEN=... uv run python scripts/benchmark_api.py ... \
  --phase cache --palette-version admission-warm-v1
```

设置 `--resource-ssh-host` 后，脚本只执行固定的只读命令，采样 NVIDIA 显存、GPU 利用率和 `comic-enhancer-api` 容器进程 RSS；采样失败会写入报告，不会伪造为 0。清单必须同时满足 `admission_eligible=true` 和至少 100 页，机器门禁才可能返回 `passed`。当前三页清单明确是 `scope=smoke`、`admission_eligible=false`，因此无论指标多好都只能返回 `smoke_only`。

## 准入门禁

快速模式进入默认路由前，必须在至少 100 页授权集上满足：

- RTX 4090 热模型 P50 不超过 2.5 秒，P95 不超过 4 秒；冷启动单列统计。
- 原始文字、气泡边界、网点和主体墨线保持清晰，失败页显示原图。
- 至少报告深色墨线、中深色结构、白底/气泡、明度误差、有效色相数和主导色占比；单一青色、金色或其他主色覆盖不能通过质量门禁。
- 连续 100 页无显存或进程内存持续增长。

三页冒烟集只证明链路可运行，未达到 100 页授权集前不得把冒烟结果当作质量准入结论。

放大档冒烟还必须确认每页输出宽高精确为原图 2 倍、`model_profile=realcugan-se-2x`，并人工检查小字号文字、气泡边界、规则网点和斜线是否出现粘连或伪纹理。不同操作系统、GPU 和 Vulkan 驱动的数据不得混为同一性能结论。
