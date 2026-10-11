# 行迹 · 一键启动（Windows PowerShell）
#
# 用法：右键「使用 PowerShell 运行」，或在本目录执行  .\start.ps1
#
# 会开两个窗口：左边跑后端（8300），右边跑前端（5273）。
# 关闭窗口即停止服务。

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "启动 行迹 · 智能行程规划 ..." -ForegroundColor Cyan

# 提示高德是否已配置 —— 没配也能跑，但会降级为本地估算
$envFile = Join-Path $root "backend\.env"
if (Test-Path $envFile) {
    Write-Host "已检测到 backend\.env，将使用高德实测数据" -ForegroundColor DarkGray
} else {
    Write-Host "未配置 backend\.env：城市搜索仅覆盖 10 座城市，里程按直线距离估算" -ForegroundColor DarkYellow
    Write-Host "  如需开启全国搜索与实测里程：copy backend\.env.example backend\.env，再填入 AMAP_API_KEY" -ForegroundColor DarkGray
}

# 后端
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$root\backend'; Write-Host '后端 · 8300' -ForegroundColor Green; .\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8300"
)

# 前端
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$root\frontend'; Write-Host '前端 · 5273' -ForegroundColor Green; npm run dev"
)

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "  前端页面   http://127.0.0.1:5273" -ForegroundColor Yellow
Write-Host "  接口文档   http://127.0.0.1:8300/docs" -ForegroundColor Yellow
Write-Host "  检索自检   http://127.0.0.1:8300/api/meta/rag" -ForegroundColor Yellow
Write-Host "  地图自检   http://127.0.0.1:8300/api/meta/amap" -ForegroundColor Yellow
