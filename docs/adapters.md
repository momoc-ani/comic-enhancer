# LoRA 索引、下载与发布

## 回退规则

每一页都执行同一规则：作品级 LoRA → 通用 LoRA → 无 LoRA。不可用包括文件缺失、SHA-256 不匹配、被禁用、基模不兼容，或缺少当前快速/质量模式的完整工作流。

`adapters/index.json` 只保存清单，不把权重提交到主项目仓库。示例作品主键：

```json
{
  "works": {
    "copy_manga:12345": {
      "adapter_id": "copy-manga-12345-v1",
      "name": "作品 12345 上色 LoRA",
      "base_model": "sd15-anime",
      "revision": "v1",
      "file": "works/copy-manga-12345-v1.safetensors",
      "sha256": "完整的 SHA-256",
      "recommended_weight": 0.55,
      "license": "项目实际许可证",
      "enabled": true,
      "work_key": "copy_manga:12345",
      "workflows": {
        "fast": "lora/copy-manga-12345-fast.json",
        "quality": "lora/copy-manga-12345-quality.json"
      }
    }
  }
}
```

LoRA 强度和加载器节点不再由 Python 注入。每个 LoRA 的 `workflows` 指向完整 API 工作流，因此不同 LoRA 可以使用不同加载器、节点编号和强度；工作流内的 `lora_name` 必须与下载到 ComfyUI `models/loras` 的相对路径一致。若只提供 `fast`，质量模式会继续尝试通用 LoRA，最后回退到质量基础工作流。

## Gitee 分发设计

建议创建一个专用 Gitee 账号和一个只存放 LoRA 的私有仓库，例如 `comic-enhancer-lora`。小型 `adapters/index.json` 放仓库分支，大型 LoRA 放 Gitee Release 附件。服务只配置该专用账号的 Token 和这个仓库名。

Gitee 的个人 Token 权限模型可能允许“仓库写入”同时包含删除能力，不能把“绝对不可删除”仅靠 Token scope 保证。因此本项目采用两层约束：

- 专用账号只加入这个 LoRA 仓库，不授予其他仓库权限。
- 服务代码只调用读取、创建 Release、上传附件、更新索引四类 API，明确没有删除接口。
- 如果必须做到密钥泄漏后也不可删除，需要在 Gitee 前增加只允许上述 HTTP 方法的发布网关；Gitee Token 本身无法提供精确到“写但不删”的保证。

Token 不提交仓库、不写入 `settings.json`，只放远端主机 `.env` 或密钥管理器中。服务启动时同步索引；处理某个作品时才懒下载对应 LoRA。

发布和下载流程：

1. 拉取索引到临时文件。
2. 验证索引版本、应用最低版本和可选签名。
3. 按 `source:source_work_id` 查找作品 LoRA。
4. 没有作品 LoRA 时查找通用 LoRA。
5. 下载到临时文件，只接受 `.safetensors`。
6. 校验大小和 SHA-256。
7. 原子移动到 ComfyUI `models/loras`。
8. 原子更新本地索引，失败则保留旧版本。

发布接口只接受 `.safetensors`，先算 SHA-256，再创建或复用 `lora` Release、上传新文件、最后更新索引。不会覆盖或删除旧附件；同名文件由 Gitee 拒绝，保留历史版本。

## 创建专用 Gitee Token

需要你在 Gitee 网页手动完成，因为密钥只应由你的账号生成，我不能替你产生或读取密钥：

1. 新建专用 Gitee 账号，或使用只拥有目标仓库的机器人账号。
2. 新建私有仓库，例如 `comic-enhancer-lora`，初始化 `adapters/index.json`。
3. 在该账号的“个人设置 -> 私人令牌”创建 Token。
4. 只选择仓库读写所需权限，不选择账号管理、删除仓库等无关权限。
5. 将 Token 只写入 `192.168.38.226` 上项目目录的 `.env`，权限设为 `chmod 600 .env`。
6. 不在聊天、Git、浏览器插件配置或 Docker 镜像中粘贴 Token。

远端 `.env` 最少需要：

```dotenv
COMIC_ENHANCER_TOKEN=漫画增强API自己的随机Token
COMIC_ENHANCER_ADMIN_TOKEN=仅管理端使用的另一组随机Token
COMIC_ENHANCER_GITEE_ENABLED=true
COMIC_ENHANCER_GITEE_OWNER=专用账号名
COMIC_ENHANCER_GITEE_REPO=comic-enhancer-lora
COMIC_ENHANCER_GITEE_TOKEN=Gitee私人令牌
```

插件只配置 `COMIC_ENHANCER_TOKEN`，不配置 `COMIC_ENHANCER_ADMIN_TOKEN` 或 `COMIC_ENHANCER_GITEE_TOKEN`。管理接口使用 `Authorization: Bearer $COMIC_ENHANCER_ADMIN_TOKEN`。

检查时只验证仓库访问和索引读取，不把 Token 作为命令行参数打印出来：

```bash
docker compose -f compose.nvidia-remote.yaml up -d --build
curl -H "Authorization: Bearer $COMIC_ENHANCER_ADMIN_TOKEN" \
  -X POST http://127.0.0.1:8765/v1/adapters/sync
```

首版不自动发布训练结果。训练、评估和发布是三个状态，只有用户主动确认后才能上传。仓库也不能包含未授权漫画原图、训练集或可反推训练数据的调试包。

作品训练输入、验证拆分和发布前准入证据见 [lora-training-input.md](lora-training-input.md)。训练图片与验证图片不上传 LoRA 仓库，自动训练完成也只能进入 `trained` 状态。

## 与拷贝漫画元数据结合

拷贝漫画页面提供标题、作者、标签和封面，可用于展示和推荐，但自动匹配仍只使用稳定作品 ID。标题相同或译名变化不能让一个作品错误下载另一个作品的 LoRA。
