param(
    [string]$RuntimeRoot = "",
    [string]$ArchiveUrl = "",
    [string]$VcRuntimeRoot = "",
    [switch]$ForceDownload
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $ProjectRoot "runtime\qwen3-vl-demo"
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$VenvRoot = Join-Path $RuntimeRoot ".venv"
$PythonExe = Join-Path $VenvRoot "Scripts\python.exe"
$RequirementsPath = Join-Path $PSScriptRoot "qwen3_vl_demo_requirements.txt"
$ReleaseTag = "b1311"
$ArchiveName = "llama-b1311-windows-rocm-gfx110X-x64.zip"
$ArchiveSize = 161656067
$ArchiveSha256 = "582f6d350055640fb88e1d46add8c4d5023eb66b646a1d32f9c7e5cab2f4c1ca"
$DownloadRoot = Join-Path $RuntimeRoot "downloads"
$ArchivePath = Join-Path $DownloadRoot $ArchiveName
$InstallRoot = Join-Path $RuntimeRoot "llamacpp-rocm\b1311-gfx110X"
$ModelPath = "E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF\Qwen3VL-4B-Instruct-Q8_0.gguf"
$MmprojPath = "E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF\mmproj-Qwen3VL-4B-Instruct-F16.gguf"

if (-not $ArchiveUrl) {
    $ArchiveUrl = if ($env:LLAMACPP_ROCM_ARCHIVE_URL) {
        $env:LLAMACPP_ROCM_ARCHIVE_URL
    } else {
        "https://github.com/lemonade-sdk/llamacpp-rocm/releases/download/$ReleaseTag/$ArchiveName"
    }
}


# 方法说明：校验文件的大小和 SHA-256 摘要。
function Test-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$ExpectedSize,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    if ((Get-Item -LiteralPath $Path).Length -ne $ExpectedSize) {
        return $false
    }
    $ActualSha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return $ActualSha256 -eq $ExpectedSha256.ToLowerInvariant()
}


# 方法说明：通过 curl 下载支持断点续传和重试的大文件。
function Save-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $Temporary = "$Target.downloading"
    & curl.exe `
        -fL `
        --retry 20 `
        --retry-all-errors `
        --retry-delay 5 `
        --connect-timeout 20 `
        --speed-time 60 `
        --speed-limit 1024 `
        -C - `
        -o $Temporary `
        $Url
    if ($LASTEXITCODE -ne 0) {
        throw "llamacpp-rocm 下载失败，退出码 $LASTEXITCODE"
    }
    Move-Item -LiteralPath $Temporary -Destination $Target -Force
}


# 方法说明：查找解压目录中的 llama-server 可执行文件。
function Find-LlamaServer {
    param([Parameter(Mandatory = $true)][string]$Root)

    $Server = Get-ChildItem -LiteralPath $Root -Recurse -Filter "llama-server.exe" |
        Select-Object -First 1
    if (-not $Server) {
        throw "运行时中没有找到 llama-server.exe：$Root"
    }
    return $Server.FullName
}


# 方法说明：按需把 Microsoft VC++ x64 运行库复制到 llama-server 目录。
function Copy-VcRuntimeLibraries {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$TargetRoot
    )

    foreach ($Name in @("MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll")) {
        $Source = Join-Path $SourceRoot $Name
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
            throw "VC++ 运行库目录缺少 $Name：$SourceRoot"
        }
        Copy-Item -LiteralPath $Source -Destination (Join-Path $TargetRoot $Name) -Force
    }
}


# 方法说明：验证 llama-server 及其本地动态库能够正常加载。
function Test-LlamaServerLaunch {
    param([Parameter(Mandatory = $true)][string]$ServerPath)

    & $ServerPath --version 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw (
            "llama-server 无法启动，退出码 $LASTEXITCODE。" +
            "请安装 Microsoft Visual C++ 2015-2022 x64 Runtime，" +
            "或通过 -VcRuntimeRoot 指定包含 MSVCP140.dll、" +
            "VCRUNTIME140.dll 和 VCRUNTIME140_1.dll 的 x64 目录。"
        )
    }
}


New-Item -ItemType Directory -Force -Path $RuntimeRoot, $DownloadRoot | Out-Null

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    & uv venv --python 3.12 $VenvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "创建 Python 3.12 虚拟环境失败"
    }
}

& $PythonExe -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
if ($LASTEXITCODE -ne 0) {
    throw "虚拟环境不是 Python 3.12：$PythonExe"
}

& uv pip install --python $PythonExe -r $RequirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "安装 Qwen3-VL demo Python 依赖失败"
}

if ($ForceDownload -or -not (Test-VerifiedFile $ArchivePath $ArchiveSize $ArchiveSha256)) {
    Write-Host "下载 llamacpp-rocm：$ArchiveUrl"
    Save-Download -Url $ArchiveUrl -Target $ArchivePath
}
if (-not (Test-VerifiedFile $ArchivePath $ArchiveSize $ArchiveSha256)) {
    throw "llamacpp-rocm 压缩包大小或 SHA-256 校验失败：$ArchivePath"
}

if ($ForceDownload -and (Test-Path -LiteralPath $InstallRoot)) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}
if (-not (Test-Path -LiteralPath $InstallRoot)) {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $InstallRoot -Force
}
$ServerPath = Find-LlamaServer -Root $InstallRoot
if ($VcRuntimeRoot) {
    Copy-VcRuntimeLibraries -SourceRoot $VcRuntimeRoot -TargetRoot (Split-Path -Parent $ServerPath)
}
Test-LlamaServerLaunch -ServerPath $ServerPath

if (-not (Test-VerifiedFile $ModelPath 4280406144 "054721f478bc5fa6beffb7f38eae575d45298f88cbb8d2f83ef675a727863eb1")) {
    throw "Qwen3-VL Q8 主模型大小或 SHA-256 校验失败：$ModelPath"
}
if (-not (Test-VerifiedFile $MmprojPath 836180256 "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331")) {
    throw "Qwen3-VL F16 mmproj 大小或 SHA-256 校验失败：$MmprojPath"
}

Write-Host ""
Write-Host "Qwen3-VL demo 环境准备完成"
Write-Host "Python: $PythonExe"
Write-Host "llama-server: $ServerPath"
Write-Host "运行示例："
Write-Host "& `"$PythonExe`" `"$PSScriptRoot\demo_qwen3_vl_gguf.py`" --image <漫画页路径>"
