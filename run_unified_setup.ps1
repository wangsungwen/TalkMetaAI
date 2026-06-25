# 💡 TalkMateAI 繁體中文 + 單埠託管一鍵配置與啟動腳本 (Windows PowerShell 專用)
$ErrorActionPreference = "Stop"

# 確保在專案根目錄下執行
$ScriptPath = $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptPath
Set-Location $ProjectRoot

Write-Host "=========================================" -ForegroundColor Green
Write-Host "🚀 正在啟動 TalkMateAI 繁體中文一鍵部署腳本..." -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green

# 1. 配置前端靜態匯出參數 (next.config.js)
$NextConfigPath = Join-Path $ProjectRoot "apps\client\next.config.js"
$NextConfigContent = @"
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: {
    unoptimized: true,
  },
};

module.exports = nextConfig;
"@

if (Test-Path $NextConfigPath) {
   Write-Host "🔧 正在自動配置前端 Next.js 靜態打包參數 (next.config.js)..." -ForegroundColor Yellow
   Set-Content -Path $NextConfigPath -Value $NextConfigContent
}
else {
   $NextConfigMjsPath = Join-Path $ProjectRoot "apps\client\next.config.mjs"
   if (Test-Path $NextConfigMjsPath) {
      Write-Host "🔧 正在自動配置前端 Next.js 靜態打包參數 (next.config.mjs)..." -ForegroundColor Yellow
      $NextConfigContent = @"
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
"@
      Set-Content -Path $NextConfigMjsPath -Value $NextConfigContent
   }
}

# 2. 進行前端打包
Write-Host "📦 正在進行前端網頁打包 (Static Export)..." -ForegroundColor Yellow
Set-Location (Join-Path $ProjectRoot "apps\client")

# 🛡️ 防禦性建置清理補丁：強制清除先前失敗的快取與可能佔用資料夾的背景 Worker 進程
if (Test-Path ".next") { Remove-Item -Recurse -Force .next -ErrorAction SilentlyContinue }
if (Test-Path "out") { Remove-Item -Recurse -Force out -ErrorAction SilentlyContinue }

pnpm run build

# 3. 補齊中文語音管線必備的「全部」 Python 套件 (防止中途 Module 缺失閃退)
Write-Host "🐍 正在補齊後端中文分詞、注音轉換與拼音正規化套件..." -ForegroundColor Yellow
Set-Location (Join-Path $ProjectRoot "apps\server")

# 💡 核心修改：全面整合安裝 jieba, g2pM, ordered_set, pypinyin, cn2an，一次到位！
& uv pip install jieba g2pM ordered_set pypinyin cn2an

# 4. 啟動單埠合一伺服器
Write-Host "=========================================" -ForegroundColor Green
Write-Host "🎉 前後端整合打包完成！正在啟動一體化主機服務..." -ForegroundColor Green
Write-Host "💡 啟動後，請開啟 ngrok 或區域網路連線！" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Green

# 使用 --no-sync 防止 uv 在啟動時將手動加載的套件降級移除，硬性指引 main.py 執行
& uv run --no-sync uvicorn main:app --host 0.0.0.0 --port 8000