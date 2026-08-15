# Real-CUGAN 平台资源

此目录按运行服务的操作系统和 CPU 架构存放 Real-CUGAN 可执行程序与
`models-se` 权重。服务只会选择当前平台对应的子目录：

```text
resource/realcugan/
  windows-x64/
    realcugan-ncnn-vulkan.exe
    vcomp140.dll
    models-se/
      up2x-no-denoise.param
      up2x-no-denoise.bin
  windows-arm64/
  linux-x64/
    realcugan-ncnn-vulkan
    models-se/
      up2x-no-denoise.param
      up2x-no-denoise.bin
  linux-arm64/
  macos-x64/
  macos-arm64/
```

平台子目录中的二进制、动态库、模型权重、上游说明和样例输出均被 Git
忽略，不随仓库发布。每个平台的资源包必须由部署者单独取得并审核
Real-CUGAN、ncnn、模型权重及附带依赖的许可证。

Linux 和 macOS 的可执行文件必须具有执行权限，例如
`chmod +x realcugan-ncnn-vulkan`；否则能力接口不会公布放大档。

放大档固定使用 `models-se`、`2x` 和 `noise=-1`。配置
`realcugan_enabled=true` 后，服务仅在可执行文件及
`up2x-no-denoise.param/.bin` 齐全时公布 `upscale` 能力。
