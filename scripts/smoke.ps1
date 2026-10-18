# 发布冒烟脚本（Plan 5 Task 3）
#
# 在隔离的 Compose 栈（project=maa-test）上跑完整用户链路，结束后清理自己创建的
# 容器/网络/卷；**不会**触碰生产栈（project=multi-agent-assistant）。
#
# 用法：
#   .\scripts\smoke.ps1              # 构建 + 启动 + 冒烟 + 清理
#   $env:KEEP = 1; .\scripts\smoke.ps1   # 保留栈便于排查

$ErrorActionPreference = "Stop"
$Project = "maa-test"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot

$ComposeArgs = @(
    "compose", "-p", $Project,
    "-f", "docker-compose.yml",
    "-f", "docker-compose.smoke.yml",
    "--profile", "test"
)

# 冒烟栈需要 JWT 密钥；未提供时用一次性随机值（不落盘）
if (-not $env:JWT_SECRET_KEY) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $env:JWT_SECRET_KEY = -join ($bytes | ForEach-Object { $_.ToString("x2") })
}
if (-not $env:POSTGRES_PASSWORD) {
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $env:POSTGRES_PASSWORD = -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Invoke-Cleanup {
    if ($env:KEEP -eq "1") {
        Write-Output "保留冒烟栈；手动清理： docker compose -p $Project -f docker-compose.yml -f docker-compose.smoke.yml --profile test down --volumes"
        return
    }
    Write-Output "清理冒烟栈..."
    & docker @ComposeArgs down --volumes --remove-orphans 2>&1 | Out-Null
}

try {
    Write-Output "构建并启动冒烟栈（project=$Project）..."
    & docker @ComposeArgs up -d --build
    if ($LASTEXITCODE -ne 0) { throw "compose up 失败（退出码 $LASTEXITCODE）" }

    Write-Output "运行冒烟客户端..."
    & docker @ComposeArgs run --rm smoke
    $status = $LASTEXITCODE

    if ($status -ne 0) {
        Write-Output "冒烟失败（退出码 $status），最近 80 行服务日志："
        & docker @ComposeArgs logs --tail 80 backend nginx
    }
    exit $status
}
finally {
    Invoke-Cleanup
    Pop-Location
}
