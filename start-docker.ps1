# 读取 server/.env 中的端口/密码配置并启动 MySQL 容器
param()
$envFile = Join-Path $PSScriptRoot 'server/.env'
if (Test-Path $envFile) {
  Get-Content $envFile | Where-Object { $_ -match '^\s*[A-Z_]+=' } | ForEach-Object {
    $kv = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($kv[0].Trim(), $kv[1].Trim())
  }
}
docker compose up -d
Write-Host "MySQL (ai-learn-mysql) 已启动，端口 ${env:MYSQL_PORT}。"
