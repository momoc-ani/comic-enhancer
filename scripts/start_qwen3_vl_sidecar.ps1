[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8080,
    [string]$Alias = "qwen3-vl-4b-instruct-q8_0",
    [string]$RuntimeRoot = "E:\project\comic-enhancer\runtime\qwen3-vl-demo",
    [string]$ModelPath = "E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF\Qwen3VL-4B-Instruct-Q8_0.gguf",
    [string]$MmprojPath = "E:\devTools\model\Qwen3-VL-4B-Instruct-GGUF\mmproj-Qwen3VL-4B-Instruct-F16.gguf",
    [string]$ApiKeyFile = "E:\project\comic-enhancer\runtime\qwen3-vl-sidecar\api-key.txt",
    [int]$ContextSize = 8192,
    [int]$ImageMinTokens = 1024,
    [int]$GpuLayers = 99
)

$ErrorActionPreference = "Stop"
$resolvedRuntime = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$server = Get-ChildItem -LiteralPath $resolvedRuntime -Filter "llama-server.exe" -Recurse |
    Sort-Object FullName |
    Select-Object -Last 1
if (-not $server) {
    throw "未找到 llama-server.exe，请先运行 scripts/setup_qwen3_vl_demo.ps1"
}
foreach ($requiredPath in @($ModelPath, $MmprojPath, $ApiKeyFile)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "缺少必需文件：$requiredPath"
    }
}
if ((Get-Item -LiteralPath $ApiKeyFile).Length -lt 32) {
    throw "API key 文件内容过短，至少使用 32 字节随机值"
}

$arguments = @(
    "-m", $ModelPath,
    "--mmproj", $MmprojPath,
    "--alias", $Alias,
    "-ngl", "$GpuLayers",
    "-c", "$ContextSize",
    "--parallel", "1",
    "--image-min-tokens", "$ImageMinTokens",
    "--host", $HostAddress,
    "--port", "$Port",
    "--api-key-file", $ApiKeyFile,
    "--jinja",
    "--offline",
    "--no-slots",
    "-lv", "3"
)

Write-Host "Qwen3-VL sidecar: http://${HostAddress}:$Port"
Write-Host "Model alias: $Alias"
Push-Location $server.DirectoryName
try {
    & $server.FullName @arguments
}
finally {
    Pop-Location
}
