# 基准测试

## 当前阶段

`works/exiled-reincarnated-heavy-knight.json` 登记了用户提供的 3 页本地非商用冒烟样本。它用于验证完整 API、真实模型路由、人物分析、输出尺寸和墨线保留，不能替代至少 100 页授权测试集，也不能把样本或生成图提交、上传或重新分发。

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
  --mode manganinja \
  --analyze
```

结果写入 `runtime/benchmarks/api/`，包括每页输出和 `report.json`。报告不包含 Token。

## 准入门禁

快速模式进入默认路由前，必须在至少 100 页授权集上满足：

- RTX 4090 热模型 P50 不超过 2.5 秒，P95 不超过 4 秒；冷启动单列统计。
- 原始文字、气泡边界、网点和主体墨线保持清晰，失败页显示原图。
- 连续 100 页无显存或进程内存持续增长。

MangaNinja 角色参考进入默认质量路由前，还必须满足：

- 逐页人工盲评角色身份、发色、服装主色和跨页一致性。
- 错误角色匹配不得进入生成；歧义检测必须继续拒绝。
- 与基础质量工作流成对比较，不因参考上色降低文字与墨线清晰度。
- 分别报告参考实际生效页、处理分格数、回退页及失败原因。

三页冒烟集只证明链路可运行。`COMIC_ENHANCER_COMFYUI_REFERENCE_ENABLED=true` 只允许显式 MangaNinja 实验档使用参考链路；未达到 100 页授权集和上述质量门禁前，不得让普通质量档自动进入该链路。
