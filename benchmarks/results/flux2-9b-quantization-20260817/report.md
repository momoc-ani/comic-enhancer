# FLUX.2 Klein 9B 量化路线结论

## 已验证

- `unsloth/FLUX.2-klein-9B-GGUF` 的 `Q4_K_M`：`5,909,829,920` 字节，SHA-256 `5489463ed96056b0bb5472abb5d1bba7055e48d574e37877acb43b407465e26f`。
- 远端挂载目录中的 `EasyUnetLoaderGGUF` 可识别该权重。
- 现有 `Klein-9B/f2k_9B_lcs_consist_20260415.safetensors` 可在 GGUF 模型上成功叠加。
- 四页热执行均值：`8.615s`。

## 与 FP8 对比

| 指标 | FP8 default | FP8 fast | Q4_K_M GGUF |
| --- | ---: | ---: | ---: |
| 热执行均值 | 6.856s | 5.185s | 8.615s |
| 显存 | 约 15.6GB | 约 15.6GB | 约 14.7GB |
| 第四页暗线保留 | 95.35% | 92.28% | 88.40% |

Q4_K_M 比 FP8 fast 慢约 66.2%，只少约 0.9GB 显存，不满足“量化提速”的目标。因此不新增量化 API 挡位，也不改变现有 9B 基线。

## 其他路线

Nunchaku INT4 的公开权重理论上更适合 4090，但当前 ComfyUI 容器是 Python 3.12、Torch 2.7、CUDA 12.6；已有 Nunchaku 1.2.0 不包含 FLUX.2 Klein，社区 `vitoom-nunchaku` wheel 只提供 Python 3.10/3.11、Torch 2.11、CUDA 12.8/13.0 组合。插件源码可挂载，运行库不能安全地直接挂载复用。后续若需要，应以独立 sidecar 部署，不改现有 ComfyUI。
