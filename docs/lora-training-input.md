# 作品 LoRA 训练输入

## 目标与边界

作品级 LoRA 用于学习作品画风、常见材质和整体配色，不替代角色身份识别、角色参考图或文字保护。训练输入、验证图片和原始页面默认只保存在用户控制的本地或训练主机；Gitee/GitHub 只允许发布经人工批准的 `safetensors`、权重哈希、许可证说明、完整工作流和不含原图的准入摘要。

## 最低输入要求

- 使用稳定的 `work_key`，例如 `copy_manga:<source_work_id>`，并登记 Bangumi、AniList 等已核对的外部作品 ID。标题和译名不能作为唯一身份。
- 每张图必须登记来源类型、原始 URL 或本地来源说明、SHA-256、权利依据和是否允许训练衍生权重。无法说明权利的图片不得进入训练集。
- 少于 10 张只能标记为实验，不得自动发布；稳定候选建议至少 30 张有效彩图，并保留至少 20% 独立验证集。
- 训练集与验证集按原始素材分组拆分。封面裁切、缩放、压缩版本和相邻近重复页必须在同一分组，避免数据泄漏。
- 优先使用授权彩页、封面、官方角色立绘和设定图。排除明显错误上色、严重压缩、低分辨率放大、第三方水印、重复字幕层和来源不明的二次编辑图。
- 每张图登记主要角色、构图类型、画面用途和颜色可信度。多人图不能只写一个角色；角色不确定时留空，不得强行绑定。
- 训练前生成去重报告、分辨率分布、角色覆盖、来源覆盖和主色分布。单一封面或单一角色占比过高时必须补样或降低发布等级。

## 建议清单

训练目录属于 `runtime/` 或独立受控存储，不进入本仓库。建议使用以下清单结构：

```json
{
  "schema_version": 1,
  "adapter_id": "work-example-v1",
  "work_key": "copy_manga:example",
  "base_model": {
    "name": "sd15-anime",
    "checkpoint": "SD1.5/SD1.5_GhostMix_V2.0.safetensors",
    "sha256": "<required>"
  },
  "rights": {
    "scope": "local-non-commercial",
    "statement": "<required>",
    "allow_derivative_lora_release": false
  },
  "images": [
    {
      "file": "images/train/0001.png",
      "sha256": "<required>",
      "split": "train",
      "source_kind": "official-character-art",
      "source_reference": "<url-or-local-record>",
      "permission": "<required>",
      "group_id": "source-art-0001",
      "characters": ["work:example:character-a"],
      "composition": "full-body",
      "color_confidence": "official"
    }
  ]
}
```

`sha256`、`permission`、`group_id`、`split` 和基模哈希是必填字段。发布系统不得从文件名、目录名或标题猜测这些值。

## 训练产物

每次训练必须固定并记录：

- 基模名称与 SHA-256、训练代码版本、随机种子、分辨率、rank/alpha、学习率、步数、优化器和推荐权重。
- 训练/验证图片数量、去重结果、角色与来源分布，不包含原始图片或可逆缩略图。
- 对应 `fast`、`quality` 完整 ComfyUI API 工作流；LoRA 节点、文件相对路径和强度全部预设在工作流内。
- 权重 `safetensors` SHA-256、许可证、训练数据权利声明和是否允许重新分发。

## 验收与发布

1. 先用独立验证集比较无 LoRA、通用 LoRA 和作品 LoRA，不得使用训练图作为效果截图或准入样本。
2. 再运行至少 100 页授权集的 `warm` 和 `cache` 基准，记录实际 LoRA 生效页、失败页、P50/P95、文字与墨线、色相多样性和资源增长。
3. 作品 LoRA 只有在机器门禁通过、人工盲评通过且权利声明允许发布时，才能进入 `approved` 状态。
4. 自动训练结束只生成 `trained` 产物，不自动上传。发布动作必须由用户明确批准，再把权重、索引、工作流和准入摘要上传到专用 Gitee/GitHub 仓库。
5. 客户端只下载索引中 `enabled=true`、基模兼容且 SHA-256 完整的版本；失败时继续按作品 LoRA、通用 LoRA、无 LoRA 回退。
