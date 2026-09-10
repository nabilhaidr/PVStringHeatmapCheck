#Requires -Version 5.1
<#
  fix-claude-mem.ps1
  Diagnosa + perbaiki hook claude-mem yang memblokir prompt Claude Code di Windows.

  Fase 1 : diagnosa, read-only, tidak menyentuh apa pun.
  Fase 2 : perbaikan (ganti port worker + restart), HANYA setelah kamu ketik Y.

  Jalankan:
    powershell -ExecutionPolicy Bypass -File "fix-claude-mem.ps1"
#>

$ErrorActionPreference = 'Continue'

$UserHome    = $env:USERPROFILE
$MemDir      = Join-Path $UserHome '.claude-mem'
$SettingsPath= Join-Path $MemDir 'settings.json'
$CacheRoot   = Join-Path $UserHome '.claude\plugins\cache\thedotmack\claude-mem'
$MarketRoot  = Join-Path $UserHome '.claude\plugins\marketplaces\thedotmack\plugin'

function Say  ($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Head ($m) { Write-Host ""; Write-Host "=== $m" -ForegroundColor Cyan }

Write-Host ""
Say "claude-mem hook doctor  --  $(Get-Date -Format 'yyyy-MM-dd HH:mm')" 'White'
Say "user home: $UserHome"

if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    Say "BERHENTI: cmdlet Get-NetTCPConnection tidak tersedia." 'Red'
    Say "Tanpa itu script tidak bisa mendeteksi ghost socket, dan kesimpulannya akan salah." 'Red'
    Say "Jalankan script ini di Windows PowerShell / PowerShell 7 biasa di Windows." 'Red'
    exit 1
}

# ---------------------------------------------------------------- 1. plugin
Head "1. Lokasi plugin"

$pluginRoot = $null
if (Test-Path $CacheRoot) {
    $pluginRoot = Get-ChildItem $CacheRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d' } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $pluginRoot -and (Test-Path $MarketRoot)) { $pluginRoot = $MarketRoot }

$filesOk = $true
if ($pluginRoot) {
    Say "  root : $pluginRoot" 'White'
    foreach ($rel in @('scripts\bun-runner.js', 'scripts\worker-service.cjs', 'hooks\hooks.json')) {
        $hit = Test-Path (Join-Path $pluginRoot $rel)
        if (-not $hit) { $filesOk = $false }
        Say ("  {0,-28} {1}" -f $rel, $(if ($hit) { 'ada' } else { 'HILANG' })) $(if ($hit) { 'Green' } else { 'Red' })
    }
} else {
    $filesOk = $false
    Say "  TIDAK KETEMU. Dicari di:" 'Red'
    Say "    $CacheRoot"
    Say "    $MarketRoot"
    Say "  Kalau plugin memang tidak ada di sini, hook-nya memanggil path kosong" 'Red'
    Say "  dan itu saja sudah cukup bikin prompt kamu diblokir." 'Red'
}

# ---------------------------------------------------------------- 2. runtime
Head "2. Runtime"

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) { Say "  node : $($node.Source)  ($(& node -v 2>&1))" 'Green' }
else       { Say "  node : TIDAK ADA di PATH Windows" 'Red' }

$bun = Get-Command bun -ErrorAction SilentlyContinue
if ($bun) { Say "  bun  : $($bun.Source)" 'Green' } else { Say "  bun  : tidak ada di PATH (biasanya tidak apa-apa)" 'Yellow' }

Say ("  panjang PATH : {0} karakter" -f $env:PATH.Length) $(if ($env:PATH.Length -gt 4000) { 'Red' } else { 'Gray' })
if ($env:PATH.Length -gt 4000) {
    Say '  PATH kamu lebih dari 4000 karakter. Hook claude-mem menggandakan PATH' 'Red'
    Say '  lewat: export PATH="$($SHELL -lc ...):$PATH" -- dan panjang PATH segini' 'Red'
    Say '  diketahui bikin resolusi perintah gagal di Windows.' 'Red'
}

# ---------------------------------------------------------------- 3. shell
Head "3. Shell yang menjalankan hook"

$wslLauncher = $false
$bash = Get-Command bash -ErrorAction SilentlyContinue
if ($bash) {
    Say "  bash : $($bash.Source)" 'White'
    if ($bash.Source -ieq (Join-Path $env:WINDIR 'System32\bash.exe')) { $wslLauncher = $true }
} else {
    Say "  bash : tidak ada di PATH Windows" 'Yellow'
}

$gitBash = 'C:\Program Files\Git\bin\bash.exe'
Say "  git bash : $(if (Test-Path $gitBash) { $gitBash } else { 'tidak ketemu di lokasi standar' })"

$wslBad = $false
if ($wslLauncher -or -not $bash) {
    Say "  -> hook kemungkinan dijalankan lewat launcher WSL, bukan Git Bash." 'Yellow'
    $env:WSL_UTF8 = '1'
    $wslOut = (& wsl.exe --list --verbose 2>&1 | Out-String) -replace "`0", ''
    if ($LASTEXITCODE -eq 0 -and $wslOut.Trim()) {
        Say "  distro WSL:" 'White'
        $wslOut.Trim().Split("`n") | ForEach-Object { Say "    $($_.TrimEnd())" }
        $defLine = $wslOut.Split("`n") | Where-Object { $_ -match '^\s*\*' } | Select-Object -First 1
        if ($defLine -match 'docker-desktop') {
            $wslBad = $true
            Say "  MASALAH: distro default WSL adalah docker-desktop." 'Red'
            Say "  VM itu tidak punya node, tidak punya cygpath, dan HOME-nya /root," 'Red'
            Say "  jadi hook gagal senyap persis seperti error yang kamu lihat." 'Red'
        }
    } else {
        Say "  wsl --list gagal / tidak ada distro terpasang." 'Yellow'
    }
}

# ---------------------------------------------------------------- 4. worker
Head "4. Worker & port"

$settings   = $null
$portInFile = $null
if (Test-Path $SettingsPath) {
    Say "  settings : $SettingsPath" 'White'
    try {
        $settings = Get-Content $SettingsPath -Raw | ConvertFrom-Json
        $keys = @($settings.PSObject.Properties.Name)
        Say "  keys     : $($keys -join ', ')"
        if ($keys -contains 'CLAUDE_MEM_WORKER_PORT') { $portInFile = [int]$settings.CLAUDE_MEM_WORKER_PORT }
    } catch {
        Say "  settings.json TIDAK BISA DIPARSE: $($_.Exception.Message)" 'Red'
    }
} else {
    Say "  settings : tidak ada ($SettingsPath)" 'Yellow'
}
Say "  port di settings : $(if ($portInFile) { $portInFile } else { '(tidak diset)' })"

$listen = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -ge 37700 -and $_.LocalPort -le 37899 })
$ghostPorts = @()
if ($listen.Count -gt 0) {
    foreach ($c in ($listen | Sort-Object LocalPort -Unique)) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) {
            Say "  :$($c.LocalPort) dipegang PID $($c.OwningProcess) ($($proc.ProcessName))" 'Green'
        } else {
            $ghostPorts += $c.LocalPort
            Say "  :$($c.LocalPort) LISTENING tapi PID $($c.OwningProcess) sudah mati -> GHOST SOCKET" 'Red'
        }
    }
} else {
    Say "  tidak ada yang LISTENING di rentang 37700-37899" 'Yellow'
}

$probePorts = @()
if ($portInFile) { $probePorts += $portInFile }
$probePorts += @($listen | Select-Object -ExpandProperty LocalPort)
$probePorts += 37777
$probePorts = $probePorts | Sort-Object -Unique

$healthyPort = $null
foreach ($pt in $probePorts) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$pt/health" -TimeoutSec 3 -UseBasicParsing
        Say "  health :$pt -> HTTP $($r.StatusCode)  SEHAT" 'Green'
        if (-not $healthyPort) { $healthyPort = $pt }
    } catch {
        Say "  health :$pt -> gagal" 'Red'
    }
}

$pidFiles = @(Get-ChildItem $MemDir -Recurse -Filter '*.pid' -ErrorAction SilentlyContinue)
foreach ($f in $pidFiles) {
    $raw = Get-Content $f.FullName -Raw -ErrorAction SilentlyContinue
    $digits = ([string]$raw) -replace '\D', ''
    if ($digits) {
        $alive = $null -ne (Get-Process -Id ([int]$digits) -ErrorAction SilentlyContinue)
        Say "  pidfile  : $($f.Name) = $digits  ($(if ($alive) { 'proses hidup' } else { 'proses SUDAH MATI' }))" $(if ($alive) { 'Green' } else { 'Red' })
    } else {
        Say "  pidfile  : $($f.Name) kosong / tidak berisi angka" 'Yellow'
    }
}

# ---------------------------------------------------------------- 5. log
Head "5. Log terakhir (ini yang disembunyikan dari pesan error)"

$logs = @(Get-ChildItem $MemDir -Recurse -Include '*.log', '*.txt' -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 3)
if ($logs.Count -eq 0) {
    Say "  tidak ada file log di $MemDir" 'Yellow'
} else {
    foreach ($l in $logs) {
        Say "  --- $($l.FullName)  ($($l.LastWriteTime))" 'White'
        Get-Content $l.FullName -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Say "      $_" }
    }
}

# ---------------------------------------------------------------- verdict
Head "KESIMPULAN"

$blockers = @()
if (-not $filesOk)          { $blockers += "File plugin tidak lengkap / tidak ketemu." }
if (-not $node)             { $blockers += "node tidak ada di PATH." }
if ($wslBad)                { $blockers += "Distro WSL default = docker-desktop (tidak punya node)." }
if ($env:PATH.Length -gt 4000) { $blockers += "PATH terlalu panjang (>4000 karakter)." }
if ($ghostPorts.Count -gt 0){ $blockers += "Ghost socket di port: $($ghostPorts -join ', ')." }
if (-not $healthyPort)      { $blockers += "Worker tidak merespons health check di port mana pun." }

if ($blockers.Count -eq 0) {
    Say "  Tidak ketemu penyebab yang jelas. Worker sehat di port $healthyPort." 'Green'
    Say "  Kalau prompt masih diblokir, jalankan perintah hook itu manual di Git Bash" 'Yellow'
    Say "  supaya error aslinya kelihatan." 'Yellow'
    exit 0
}
foreach ($b in $blockers) { Say "  - $b" 'Red' }

if ($wslBad) {
    Say ""
    Say "  Script ini TIDAK akan mengubah distro default WSL kamu -- itu setting global" 'Yellow'
    Say "  yang bisa memengaruhi tool lain. Perbaiki sendiri dengan:" 'Yellow'
    Say "      wsl --list --verbose" 'White'
    Say "      wsl --set-default <nama-distro-yang-punya-node>" 'White'
}

# ---------------------------------------------------------------- fix plan
if ($healthyPort) {
    Say ""
    Say "  Worker sebenarnya SEHAT di port $healthyPort, jadi mengganti port tidak akan" 'Yellow'
    Say "  menolong. Masalahnya ada di sisi shell/runtime di atas. Berhenti di sini." 'Yellow'
    exit 1
}
if (-not $pluginRoot -or -not $node) {
    Say ""
    Say "  Tidak bisa restart worker tanpa node + file plugin. Berhenti di sini." 'Red'
    exit 1
}

$used = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort)
$newPort = 37800..37899 | Where-Object { $used -notcontains $_ } | Select-Object -First 1

Head "RENCANA PERBAIKAN"
Say "  1. Backup  $SettingsPath  -> .bak-<timestamp>"
Say "  2. Set CLAUDE_MEM_WORKER_PORT = $newPort  (menghindari ghost socket)"
Say "  3. Rename file .pid yang basi -> .pid.bak"
Say "  4. Start ulang worker lewat: node bun-runner.js worker-service.cjs start"
Say "  5. Cek health lagi di port $newPort"
Write-Host ""
$answer = Read-Host "  Lanjut? ketik Y untuk jalan, apa pun yang lain untuk batal"
if ($answer -notmatch '^[Yy]') { Say "  Dibatalkan. Tidak ada yang diubah." 'Yellow'; exit 0 }

Head "MENJALANKAN PERBAIKAN"

if (-not (Test-Path $MemDir)) { New-Item -ItemType Directory -Path $MemDir -Force | Out-Null }
if (Test-Path $SettingsPath) {
    $bak = "$SettingsPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item $SettingsPath $bak -Force
    Say "  backup   : $bak" 'Green'
}
if (-not $settings) { $settings = [pscustomobject]@{} }
if ($settings.PSObject.Properties.Name -contains 'CLAUDE_MEM_WORKER_PORT') {
    $settings.CLAUDE_MEM_WORKER_PORT = $newPort
} else {
    $settings | Add-Member -NotePropertyName CLAUDE_MEM_WORKER_PORT -NotePropertyValue $newPort
}
$settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsPath -Encoding UTF8
Say "  port     : CLAUDE_MEM_WORKER_PORT = $newPort" 'Green'

foreach ($f in $pidFiles) {
    Rename-Item $f.FullName "$($f.Name).bak" -Force -ErrorAction SilentlyContinue
    Say "  pidfile  : $($f.Name) -> $($f.Name).bak" 'Green'
}

$env:CLAUDE_MEM_WORKER_PORT = $newPort
$runner = Join-Path $pluginRoot 'scripts\bun-runner.js'
$svc    = Join-Path $pluginRoot 'scripts\worker-service.cjs'
Say "  start    : node `"$runner`" `"$svc`" start" 'White'
& node $runner $svc start 2>&1 | ForEach-Object { Say "      $_" }
Say "  exit code: $LASTEXITCODE" $(if ($LASTEXITCODE -eq 0) { 'Green' } else { 'Red' })

Start-Sleep -Seconds 4
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$newPort/health" -TimeoutSec 5 -UseBasicParsing
    Say ""
    Say "  BERHASIL: worker sehat di port $newPort (HTTP $($r.StatusCode))." 'Green'
    Say "  Restart Claude Code, lalu coba kirim prompt lagi." 'Green'
} catch {
    Say ""
    Say "  GAGAL: worker masih tidak merespons di port $newPort." 'Red'
    Say "  Penyebab paling mungkin: ghost socket bertahan sampai reboot Windows." 'Red'
    Say "  Coba reboot Windows (bukan cuma restart Claude Code), lalu jalankan script ini lagi." 'Red'
    Say ""
    Say "  Kalau tetap gagal, matikan hook-nya supaya kamu tidak diblokir:" 'Yellow'
    Say "      ren `"$pluginRoot\hooks\hooks.json`" hooks.json.disabled" 'White'
    Say "      ren `"$pluginRoot\.mcp.json`" .mcp.json.disabled" 'White'
    exit 1
}
