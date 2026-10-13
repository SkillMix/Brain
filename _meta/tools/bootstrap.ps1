# Bootstrap (Windows): find a Python 3.10 or newer, or install one without admin rights, then
# run start.py with every argument given. The Microsoft Store's `python` stub fails the check
# below and is skipped. uv comes from its official release, pinned and checked against its
# SHA-256 before use; it installs a Python under your home folder. Run it for this one process
# only, without changing the machine's execution policy:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File _meta\tools\bootstrap.ps1 [start.py arguments ...]
$ErrorActionPreference = 'Stop'
$start = if ($env:BOOTSTRAP_START) { $env:BOOTSTRAP_START } else { Join-Path $PSScriptRoot 'start.py' }
$tools = Join-Path $HOME '.spaces\tools'

function Test-Python([string]$cmd, [string[]]$pre) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { return $false }
  try {
    & $cmd @pre -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
  } catch { return $false }
}

foreach ($candidate in @(@{cmd = 'py'; pre = @('-3')}, @{cmd = 'python'; pre = @()}, @{cmd = 'python3'; pre = @()})) {
  $pre = $candidate.pre
  if (Test-Python $candidate.cmd $pre) {
    & $candidate.cmd @pre $start @args
    exit $LASTEXITCODE
  }
}

if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
  $url = 'https://github.com/astral-sh/uv/releases/download/0.12.18/uv-aarch64-pc-windows-msvc.zip'
  $sha = '17f27b1c64eacc757ae603579f116a014881e486c5e79ae81877980d4699e943'
} else {
  $url = 'https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-pc-windows-msvc.zip'
  $sha = 'cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff'
}

$uv = Join-Path $tools 'uv\uv.exe'
if (-not (Test-Path $uv)) {
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $zip = Join-Path $tmp 'uv.zip'
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  $got = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
  if ($got -ne $sha) {
    Remove-Item -Recurse -Force $tmp
    Write-Host 'bootstrap: the uv download did not match its published checksum; nothing was installed'
    exit 1
  }
  New-Item -ItemType Directory -Force -Path (Join-Path $tools 'uv') | Out-Null
  Expand-Archive -Path $zip -DestinationPath (Join-Path $tools 'uv') -Force
  Remove-Item -Recurse -Force $tmp
}
& $uv python install 3.12 | Out-Host
$py = (& $uv python find 3.12 | Select-Object -First 1).Trim()
& $py $start @args
exit $LASTEXITCODE
