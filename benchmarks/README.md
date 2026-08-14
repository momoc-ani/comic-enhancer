# 基准测试

## 当前阶段

`works/exiled-reincarnated-heavy-knight.json` 登记了用户提供的 3 页本地非商用冒烟样本。它用于验证完整 API、真实模型路由、人物分析、输出尺寸和墨线保留，不能替代至少 100 页授权测试集，也不能把样本或生成图提交、上传或重新分发。

2026-08-14 的 RTX 4090 MangaNinja 热推理冒烟为：3 页全部成功、全部实际使用角色参考、P50 `21.810s`、P95 `21.885s`；深色墨线最低保留 `100%`、中深色结构最低 `99.94%`、白底最低 `99.80%`。有效色相中位数为 6，但最差页主导色占比仍为 `83.39%`，超过暂定 `80%` 门禁，因此当前画质仍不具备准入结论。1 秒间隔资源监控取得 49 个有效样本，GPU 峰值约 `18.1 GiB`、利用率峰值 `100%`；本轮首尾显存差约 `4.0 GiB` 反映模型驻留变化，不能用 3 页推导内存泄漏结论。

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
  --base-url http://192.168.38.226:8765 \
  --manifest benchmarks/works/exiled-reincarnated-heavy-knight.json \
  --mode manganinja --phase warm --analyze \
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

MangaNinja 角色参考进入默认质量路由前，还必须满足：

- 逐页人工盲评角色身份、发色、服装主色和跨页一致性。
- 错误角色匹配不得进入生成；歧义检测必须继续拒绝。
- 与基础质量工作流成对比较，不因参考上色降低文字与墨线清晰度。
- 分别报告参考实际生效页、处理分格数、回退页及失败原因。

三页冒烟集只证明链路可运行。`COMIC_ENHANCER_COMFYUI_REFERENCE_ENABLED=true` 只允许显式 MangaNinja 实验档使用参考链路；未达到 100 页授权集和上述质量门禁前，不得让普通质量档自动进入该链路。
