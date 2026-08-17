# FLUX.2 Klein 4B LoRA 上色优化报告

## 1. 最终结论

在四张固定漫画页上完成了 LoRA 强度、提示词长度、参考图条件分辨率和 CFG 的分阶段筛选。最终候选为：

- `FLUX.2 Klein 4B FP8`
- `Qwen3 4B`
- `f2k_4B_consist_20260314.safetensors`
- LoRA 强度 `0.35`
- 原图条件 `0.85 MP`
- 每张人物参考图 `0.15 MP`
- `CFG=1.3`、`steps=4`、`Euler`、`seed=20260814`
- 紧凑强锁定提示词和明确负面约束
- ComfyUI 工作流直出，无本地结构后处理

四页平均饱和色覆盖为 `32.96%`，接近 9B LoRA 的 `31.28%`；暗线保留为 `95.04%`，低于 9B 的 `96.80%`，但白区保留为 `86.12%`，高于 9B 的 `81.72%`。扩大查看局部细节后，仍能观察到原空 latent 工作流凭空补充人物和服装信息，因此早期“未见明显新增内容”的判断不再成立。

结论：原空 latent 4B 候选颜色和速度接近目标，但不能满足“尽可能少重绘”的硬约束。最终建议把 `4 steps / source latent / denoise=0.65` 定位为快速结构稳定档，把 9B LoRA 保留为当前画质档；Qwen Image Edit 2511 没有找到兼顾颜色覆盖和结构保真的参数窗口。追加验证见第 8 节。

![最终四页对照](images/final-strength-comparison-grid.jpg)

## 2. 最终参数与 9B 对比

| 参数 | 4B 优化候选 | 9B 当前候选 |
| --- | --- | --- |
| UNet | `flux-2-klein-4b-fp8.safetensors` | `FLUX.2-klein-9b-fp8-v2_flux2 Klein b9.safetensors` |
| 文本编码器 | `qwen_3_4b.safetensors` | `qwen_3_8b_fp8mixed.safetensors` |
| LoRA | `f2k_4B_consist_20260314.safetensors` | `Klein-9B/f2k_9B_lcs_consist_20260415.safetensors` |
| LoRA 强度 | `0.35` | `0.6` |
| 原图条件 | `0.85 MP` | `0.85 MP` |
| 单张参考图条件 | `0.15 MP` | `0.25 MP` |
| CFG | `1.3` | `1.0` |
| 采样 | `4 steps / Euler` | `4 steps / Euler` |
| 提示词 | 紧凑强锁定和调色板注册 | 原长版结构锁定提示词 |
| 负面约束 | CFG 1.3 下参与引导 | CFG 1.0 下作用很弱 |

## 3. 四页汇总指标

| 组合 | 平均耗时 | 热推理平均 | 灰度 MAE ↓ | 暗线保留 ↑ | 白区保留 ↑ | 饱和色覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4B 无 LoRA 基线 | 5.938s | 4.898s | 32.857 | 90.57% | 78.47% | 35.17% |
| 4B LoRA 0.6 初始参数 | 5.707s | 4.747s | 22.169 | 94.22% | 90.31% | 14.35% |
| **4B LoRA 0.35 优化候选** | **6.292s** | **6.077s** | **27.498** | **95.04%** | **86.12%** | **32.96%** |
| 9B LoRA 0.6 | 8.838s | 7.799s | 32.751 | 96.80% | 81.72% | 31.28% |

说明：4B 优化候选相对 9B 的热推理约快 `22%`。耗时来自本次远端实测，模型缓存和 LoRA 切换状态不同，不能视为严格冷启动性能结论。

## 4. 每页结果

### 原图 1

| 原图 | 4B LoRA 0.6 初始 | 4B LoRA 0.35 优化 | 9B LoRA 0.6 |
| --- | --- | --- | --- |
| <img src="images/source-page-1.webp" alt="原图1" width="220"> | <img src="images/initial-4b-lora-0.6-page-1.png" alt="4B LoRA初始原图1" width="220"> | <img src="images/optimized-4b-page-1.png" alt="4B LoRA优化原图1" width="220"> | <img src="images/target-9b-page-1.png" alt="9B LoRA原图1" width="220"> |

| 组合 | 耗时 | 灰度 MAE | 暗线 | 白区 | 饱和色覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4B 优化 | 6.938s | 32.209 | 97.12% | 80.25% | 33.86% |
| 9B LoRA | 11.954s | 34.919 | 98.25% | 81.43% | 28.47% |

### 原图 2

| 原图 | 4B LoRA 0.6 初始 | 4B LoRA 0.35 优化 | 9B LoRA 0.6 |
| --- | --- | --- | --- |
| <img src="images/source-page-2.webp" alt="原图2" width="220"> | <img src="images/initial-4b-lora-0.6-page-2.png" alt="4B LoRA初始原图2" width="220"> | <img src="images/optimized-4b-page-2.png" alt="4B LoRA优化原图2" width="220"> | <img src="images/target-9b-page-2.png" alt="9B LoRA原图2" width="220"> |

| 组合 | 耗时 | 灰度 MAE | 暗线 | 白区 | 饱和色覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4B 优化 | 6.760s | 35.283 | 94.69% | 79.64% | 34.98% |
| 9B LoRA | 7.671s | 38.496 | 95.89% | 80.36% | 33.76% |

### 原图 3

| 原图 | 4B LoRA 0.6 初始 | 4B LoRA 0.35 优化 | 9B LoRA 0.6 |
| --- | --- | --- | --- |
| <img src="images/source-page-3.webp" alt="原图3" width="220"> | <img src="images/initial-4b-lora-0.6-page-3.png" alt="4B LoRA初始原图3" width="220"> | <img src="images/optimized-4b-page-3.png" alt="4B LoRA优化原图3" width="220"> | <img src="images/target-9b-page-3.png" alt="9B LoRA原图3" width="220"> |

| 组合 | 耗时 | 灰度 MAE | 暗线 | 白区 | 饱和色覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4B 优化 | 5.592s | 21.582 | 95.44% | 92.56% | 23.88% |
| 9B LoRA | 7.871s | 23.833 | 97.71% | 89.63% | 23.97% |

### 原图 4

| 原图 | 4B LoRA 0.6 初始 | 4B LoRA 0.35 优化 | 9B LoRA 0.6 |
| --- | --- | --- | --- |
| <img src="images/source-page-4.webp" alt="原图4" width="220"> | <img src="images/initial-4b-lora-0.6-page-4.png" alt="4B LoRA初始原图4" width="220"> | <img src="images/optimized-4b-page-4.png" alt="4B LoRA优化原图4" width="220"> | <img src="images/target-9b-page-4.png" alt="9B LoRA原图4" width="220"> |

| 组合 | 耗时 | 灰度 MAE | 暗线 | 白区 | 饱和色覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4B 优化 | 5.880s | 20.919 | 92.92% | 92.04% | 39.10% |
| 9B LoRA | 7.855s | 33.755 | 95.35% | 75.48% | 38.90% |

## 5. 参数筛选过程

### 5.1 初始 LoRA 0.6

LoRA `0.6` 一致性较强，但平均饱和色覆盖从无 LoRA 的 `35.17%` 降到 `14.35%`，第 4 页除角色局部外大面积退回黑白，因此判定强度过高。

### 5.2 长版颜色提示词

在 LoRA `0.2 / 0.3 / 0.4` 上分别比较原提示词与追加长版颜色提示词。长版提示词在所有强度下都降低颜色覆盖，说明 4B 对过长、多层级约束响应更保守，因此放弃追加式提示词。

![长版提示词筛选](images/prompt-comparison-grid.jpg)

### 5.3 强锁定提示词、参考图权重和 CFG

将提示词改写为紧凑版，并明确禁止以下行为：

- 新增、复制或补全人物
- 补全裁切腿部或被遮挡身体
- 给远景人物新增脸、眼睛、肢体、手和服装细节
- 复制参考图姿态、身体、轮廓或服装结构
- 在角色之间交换或混合调色板
- 改变发型轮廓、服装几何、面板、文字和构图

同时把每张参考图从 `0.25 MP` 降到 `0.10 / 0.15 / 0.20 MP`，并比较 CFG `1.0 / 1.3`。

| 参考图/张 | CFG | 平均耗时 | 灰度 MAE | 暗线 | 白区 | 饱和色覆盖 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.10 MP | 1.0 | 4.561s | 25.554 | 94.14% | 87.27% | 29.51% |
| 0.15 MP | 1.0 | 3.697s | 25.624 | 93.97% | 87.40% | 28.45% |
| 0.20 MP | 1.0 | 3.926s | 25.691 | 93.92% | 87.43% | 28.21% |
| **0.15 MP** | **1.3** | **6.084s** | **27.666** | **94.95%** | **86.11%** | **34.54%** |
| 0.20 MP | 1.3 | 6.276s | 27.790 | 95.07% | 86.23% | 33.32% |

![参考图权重与CFG筛选](images/hardlock-parameter-comparison-grid.jpg)

### 5.4 最终 LoRA 强度

固定参考图 `0.15 MP`、CFG `1.3` 后，比较 LoRA `0.2 / 0.25 / 0.30 / 0.35 / 0.40`。`0.2` 颜色更强但结构余量较小，`0.4` 开始回灰；完整四页候选缩小为 `0.25 / 0.30 / 0.35`。

| LoRA | 灰度 MAE | 暗线 | 白区 | 饱和色覆盖 | 结论 |
| ---: | ---: | ---: | ---: | ---: | --- |
| 0.25 | 28.653 | 95.28% | 85.26% | 36.34% | 色彩最强，结构余量略小 |
| 0.30 | 27.666 | 94.95% | 86.11% | 34.54% | 平衡候选 |
| **0.35** | **27.498** | **95.04%** | **86.12%** | **32.96%** | 最接近 9B 色彩强度，最终候选 |

## 6. 强约束的边界

负面提示词在 `CFG=1.0` 下作用很弱，提高到 `1.3` 后才真正参与引导。即使如此，当前工作流仍然从空 latent 生成，提示词和 LoRA 不能提供数学意义上的像素锁定。如果扩大测试集后仍出现新增人物，应优先验证兼容 FLUX.2 Klein 的线稿 ControlNet 或源图 latent 低 denoise，而不是继续增加提示词长度。

## 7. 文件索引

- [结果图片目录](images/)
- [4B 优化候选机器报告](data/optimized-4b-report.json)
- [最终强度筛选数据](data/strength-sweep-report.json)
- [强锁定参数筛选数据](data/hardlock-sweep-report.json)
- [早期颜色提示词筛选数据](data/prompt-sweep-report.json)
- [原始模型对比数据](data/model-comparison-report.json)

## 8. 结构约束与 Qwen Edit 追加验证

### 8.1 最终排序

| 排名 | 组合 | 定位 | 实测结论 |
| ---: | --- | --- | --- |
| 1 | FLUX.2 Klein 9B LoRA，4 步 | 当前画质档 | 色彩完成度和复杂参考关系理解最好，热推理平均 `7.799s` |
| 2 | FLUX.2 Klein 4B LoRA，source latent，`denoise=0.65`，4 步 | 快速结构稳定档 | 原图 latent 明显减少新增内容；独立工作流热运行约 `5.287s`，颜色覆盖低于空 latent |
| 3 | FLUX.2 Klein 4B LoRA，空 latent，4 步 | 仅保留实验 | 热推理平均 `6.077s`、颜色较强，但局部仍会凭空补充人物或服装信息 |
| 4 | Qwen Image Edit 2511 Lightning，4 步 | 不采用 | `denoise=1.0` 重绘明显；`denoise=0.65` 仍改写文字和线条且颜色接近消失 |

4B source latent 提高到 `6 steps / denoise=0.70` 后，四页平均耗时约 `8.332s`，已经慢于 9B 的热推理平均 `7.799s`，因此没有作为最终候选。其四页平均饱和色覆盖约 `27.25%`，仍未形成足以抵消耗时劣势的画质收益。

### 8.2 Qwen Image Edit 2511

可直接运行的工作流为 [`workflows/qwen-image-edit-2511-lightning-colorize.json`](../../../workflows/qwen-image-edit-2511-lightning-colorize.json)，模型组合如下：

- `qwen_image_edit_2511_fp8mixed.safetensors`
- `qwen_2.5_vl_7b_fp8_scaled.safetensors`
- `qwen_image_vae.safetensors`
- `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors`
- `4 steps / CFG 1.0 / Euler / simple`

`denoise=1.0` 时颜色完成度高，但四页都表现为重新解释原图：人物脸部和服装、建筑轮廓、背景细节及文字均存在变化。把输入改为 source latent 并降低到 `denoise=0.65` 后，四页仍全部成功运行，平均耗时 `14.561s`，但质量指标和人工检查都未通过：

| 页面 | 耗时 | 灰度 MAE ↓ | 暗线保留 ↑ | 白区保留 ↑ | 饱和色覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 原图 1 | 14.724s | 41.463 | 70.95% | 83.50% | 0.63% |
| 原图 2 | 14.518s | 45.845 | 44.58% | 82.06% | 1.69% |
| 原图 3 | 14.541s | 31.323 | 59.10% | 87.61% | 1.14% |
| 原图 4 | 14.461s | 36.885 | 56.87% | 84.73% | 0.78% |

原图 3 的右侧文本框在 `denoise=0.65` 下仍被改写，原图 1、3 则基本退回灰度。Qwen Edit 这组低降噪测试平均 `14.561s`，明显慢于 4B source latent 热运行约 `5.287s` 和 9B Klein 热推理约 `7.799s`。继续提高到 `0.75/0.85` 只会沿着“颜色增加、重绘增强”的方向移动，因此按停止条件不再扩大扫描。Qwen Edit 可以作为通用重绘式图片编辑器使用，但不适合本项目“只上色、不修改线稿、文字和页面内容”的主链路。

### 8.3 选型建议

- 默认画质优先：9B LoRA，4 步。
- 需要更快且优先减少新增内容：4B LoRA，source latent，`denoise=0.65`，4 步。
- 可直接导入的 4B source latent 工作流：`workflows/flux2-klein-4b-source-latent-colorize.json`。
- 4B 空 latent 只用于可接受少量重绘、强调色彩覆盖的实验场景。
- Qwen Image Edit 2511、Anima Base 和 Anima 2.9B 均不进入默认路由。
