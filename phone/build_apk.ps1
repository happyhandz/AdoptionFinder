# Rebuilds the phone app, copies it to phone\AdoptionFinder.apk and writes phone\version.json.
#   powershell -ExecutionPolicy Bypass -File phone\build_apk.ps1
#   powershell -ExecutionPolicy Bypass -File phone\build_apk.ps1 -Server http://192.168.1.20:8000
#   powershell -ExecutionPolicy Bypass -File phone\build_apk.ps1 -NoBump      (rebuild without raising the version)
#
# * Every build raises the version number by one, so a phone that already has the app can tell the new
#   build is newer: in the app, menu > "Check for app update" downloads it from this PC and installs it.
# * The app opens this PC's address on its own (no pairing). By default that is the Tailscale address when
#   Tailscale is running (works at home and away), otherwise this PC's home-network address. Pass -Server to override.
# * Uses the JDK 17 / Android SDK / Gradle in the tools\ folder (no system-wide install needed).
param([string]$Server = "", [switch]$NoBump)

$project = Split-Path -Parent $PSScriptRoot
$utf8 = New-Object System.Text.UTF8Encoding($false)    # no byte-order mark (Windows PowerShell's -Encoding utf8 adds one)

# ---- which address the app should open ----
if (-not $Server) {
    $ts = $null
    foreach ($exe in "C:\Program Files\Tailscale\tailscale.exe", "C:\Program Files (x86)\Tailscale\tailscale.exe") {
        if (Test-Path $exe) { $ts = & $exe ip -4 2>$null | Select-Object -First 1; if ($ts) { break } }
    }
    if ($ts) {
        $Server = "http://${ts}:8000"
    } else {
        $ip = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1 -ExpandProperty IPv4Address | Select-Object -First 1 -ExpandProperty IPAddress
        if (-not $ip) { Write-Host "Could not detect this PC's address; pass -Server http://<address>:8000" -ForegroundColor Red; exit 1 }
        $Server = "http://${ip}:8000"
    }
}

# ---- version number ----
$buildFile = "$project\android\app\build.gradle"
$text = [System.IO.File]::ReadAllText($buildFile)
$code = [int]([regex]::Match($text, 'versionCode (\d+)').Groups[1].Value)
if (-not $NoBump) {
    $code = $code + 1
    $text = $text -replace 'versionCode \d+', "versionCode $code" -replace "versionName '[^']*'", "versionName '1.$($code - 1)'"
    [System.IO.File]::WriteAllText($buildFile, $text, $utf8)
}
$name = [regex]::Match($text, "versionName '([^']*)'").Groups[1].Value
Write-Host "App version $name (build $code) will open: $Server"

$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<!-- Written by phone\build_apk.ps1 with this PC's address at build time. -->
<resources>
    <string name="default_server">$Server</string>
</resources>
"@
[System.IO.File]::WriteAllText("$project\android\app\src\main\res\values\server.xml", $xml, $utf8)

# ---- build ----
$tools = Join-Path $project "tools"
$env:JAVA_HOME = Get-ChildItem "$tools\jdk" -Directory | Select-Object -First 1 -ExpandProperty FullName
$env:ANDROID_HOME = "$tools\android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:GRADLE_USER_HOME = "$tools\gradle-home"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
$gradleBat = Get-ChildItem "$tools\gradle" -Directory | Select-Object -First 1 | ForEach-Object { "$($_.FullName)\bin\gradle.bat" }

Push-Location "$project\android"
& $gradleBat --no-daemon --console=plain assembleDebug
$ok = $LASTEXITCODE -eq 0
Pop-Location
if (-not $ok) { Write-Host "Build failed" -ForegroundColor Red; exit 1 }

$apk = "$PSScriptRoot\AdoptionFinder.apk"
Copy-Item "$project\android\app\build\outputs\apk\debug\app-debug.apk" $apk -Force
$info = [ordered]@{
    versionCode = $code
    versionName = $name
    size        = (Get-Item $apk).Length
    sha256      = (Get-FileHash $apk -Algorithm SHA256).Hash.ToLower()
    built_at    = (Get-Date).ToString("s")
    server      = $Server
}
[System.IO.File]::WriteAllText("$PSScriptRoot\version.json", ($info | ConvertTo-Json), $utf8)
Write-Host "Built phone\AdoptionFinder.apk (version $name, build $code). Phones running the app can now use menu > Check for app update."
