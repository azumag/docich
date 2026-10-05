#requires -Version 7.0\n\nparam(
  [string]$WorkerBaseUrl = $env:DOCICH_DISCORD_VOICE_WORKER_BASE_URL,
  [string]$LogPath = "",
  [double]$MinMinutes = 0,
  [double]$StopAfterMinutes = 0,
  [switch]$RequireInterrupt,
  [switch]$RequireReconnect,
  [switch]$AllowLoopbackVoicevox,
  [switch]$SkipInstall,
  [switch]$ProvisionBridgeSecret
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Env([string]$Name) {
  $value = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "Missing required process environment variable: $Name"
  }
  return $value
}

function New-BridgeSecret {
  $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(48)
  try {
    return ([Convert]::ToBase64String($bytes)).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  }
  finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}

function Invoke-BridgePreflight([string]$Url, [string]$Token) {
  $response = Invoke-WebRequest -Uri $Url -Method Post -Headers @{ Authorization = "Bearer $Token" } -ContentType "application/json" -Body "{}" -SkipHttpErrorCheck -MaximumRedirection 0
  if ([int]$response.StatusCode -ne 400) {
    throw "Voice bridge preflight failed with HTTP $($response.StatusCode). Expected authenticated invalid-request 400."
  }
}

function First-ConfiguredUrl([string]$Raw) {
  $items = @($Raw -split "[,\s]+" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
  if ($items.Count -lt 1) {
    throw "VOICEVOX_URLS has no endpoint."
  }
  return [string]$items[0]
}

$required = @(
  "DOCICH_DISCORD_TOKEN",
  "DOCICH_DISCORD_VOICE_GUILD_ID",
  "DOCICH_DISCORD_VOICE_CHANNEL_ID",
  "DOCICH_DISCORD_VOICE_RECEIVE_USER_ID",
  "DOCICH_DISCORD_VOICE_CF_ACCOUNT_ID",
  "DOCICH_DISCORD_VOICE_CF_API_TOKEN",
  "VOICEVOX_URLS"
)
foreach ($name in $required) {
  [void](Require-Env $name)
}

if ([string]::IsNullOrWhiteSpace($WorkerBaseUrl)) {
  throw "Set DOCICH_DISCORD_VOICE_WORKER_BASE_URL or pass -WorkerBaseUrl."
}
$workerRoot = $WorkerBaseUrl.TrimEnd("/")
if (-not $workerRoot.StartsWith("https://")) {
  throw "WorkerBaseUrl must be HTTPS."
}

$voicevoxUrl = First-ConfiguredUrl (Require-Env "VOICEVOX_URLS")
$voicevoxUri = [Uri]$voicevoxUrl
$loopbackHosts = @("localhost", "127.0.0.1", "::1", "0.0.0.0")
$isLoopback = $loopbackHosts -contains $voicevoxUri.Host.ToLowerInvariant()
if ($isLoopback -and -not $AllowLoopbackVoicevox) {
  throw "VOICEVOX_URLS points to loopback. Re-run with -AllowLoopbackVoicevox for an intentional same-host engine."
}

$env:DOCICH_DISCORD_VOICE_ENABLED = "1"
$env:DOCICH_DISCORD_VOICE_RECEIVE_ENABLED = "1"
$env:DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED = "1"
$env:DOCICH_DISCORD_VOICE_TTS_ENABLED = "1"
$env:DOCICH_DISCORD_VOICE_TRANSCRIPT_DEBUG = "0"
$env:DOCICH_DISCORD_VOICE_REPLY_DEBUG = "0"
$env:DOCICH_DISCORD_VOICE_VOICEVOX_ALLOW_LOOPBACK = $(if ($AllowLoopbackVoicevox) { "1" } else { "0" })
$env:DOCICH_DISCORD_VOICE_CHAT_URL = "$workerRoot/voice/reply"

$runtimeDir = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $runtimeDir "../..")).Path
$workerDir = Join-Path $repoRoot "workers/discord-chat"

if ([string]::IsNullOrWhiteSpace($LogPath)) {
  $stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMdd-HHmmss")
  $LogPath = Join-Path $runtimeDir "acceptance-logs/voice-acceptance-$stamp.jsonl"
}
$fullLogPath = [System.IO.Path]::GetFullPath($LogPath)

$generatedSecret = $null
$originalChatToken = [Environment]::GetEnvironmentVariable("DOCICH_DISCORD_VOICE_CHAT_TOKEN", "Process")

try {
  if ($ProvisionBridgeSecret) {
    $generatedSecret = New-BridgeSecret
    $env:DOCICH_DISCORD_VOICE_CHAT_TOKEN = $generatedSecret

    Push-Location $workerDir
    try {
      & npm install --no-package-lock --no-audit --no-fund
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to install pinned Discord Worker dependencies."
      }
      $generatedSecret | & npx --no-install wrangler secret put DISCORD_VOICE_INTERNAL_TOKEN --name docich-discord-chat
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to provision DISCORD_VOICE_INTERNAL_TOKEN."
      }
    }
    finally {
      Pop-Location
    }
  }
  elseif ([string]::IsNullOrWhiteSpace($originalChatToken)) {
    throw "Set DOCICH_DISCORD_VOICE_CHAT_TOKEN or re-run with -ProvisionBridgeSecret."
  }

  $chatToken = Require-Env "DOCICH_DISCORD_VOICE_CHAT_TOKEN"
  Invoke-BridgePreflight $env:DOCICH_DISCORD_VOICE_CHAT_URL $chatToken

  $versionUrl = $voicevoxUrl.TrimEnd("/") + "/version"
  $voicevoxResponse = Invoke-WebRequest -Uri $versionUrl -Method Get -SkipHttpErrorCheck -MaximumRedirection 0
  if ([int]$voicevoxResponse.StatusCode -lt 200 -or [int]$voicevoxResponse.StatusCode -ge 300) {
    throw "VOICEVOX preflight failed with HTTP $($voicevoxResponse.StatusCode)."
  }

  Push-Location $runtimeDir
  try {
    if (-not $SkipInstall) {
      & npm install --no-audit --no-fund
      if ($LASTEXITCODE -ne 0) {
        throw "npm install failed."
      }
    }

    & npm run check:live
    if ($LASTEXITCODE -ne 0) {
      throw "Live dependency check failed."
    }

    $runnerArgs = @(
      "acceptance-runner.mjs",
      "--log", $fullLogPath,
      "--min-minutes", ([string]$MinMinutes)
    )
    if ($StopAfterMinutes -gt 0) {
      $runnerArgs += @("--stop-after-minutes", ([string]$StopAfterMinutes))
    }
    if ($RequireInterrupt) {
      $runnerArgs += "--require-interrupt"
    }
    if ($RequireReconnect) {
      $runnerArgs += "--require-reconnect"
    }

    Write-Host "Voice acceptance started. Normal output is sanitized. Log: $fullLogPath"
    if ($StopAfterMinutes -le 0) {
      Write-Host "Press Ctrl+C once to request a graceful stop and print the acceptance summary."
    }

    & node @runnerArgs
    $runnerExit = $LASTEXITCODE
    if ($runnerExit -ne 0) {
      throw "Voice acceptance did not satisfy the requested gates. Summary exit code: $runnerExit"
    }
  }
  finally {
    Pop-Location
  }
}
finally {
  if ($null -ne $generatedSecret) {
    if ([string]::IsNullOrWhiteSpace($originalChatToken)) {
      $env:DOCICH_DISCORD_VOICE_CHAT_TOKEN = $null
    }
    else {
      $env:DOCICH_DISCORD_VOICE_CHAT_TOKEN = $originalChatToken
    }
    $generatedSecret = $null
    [System.GC]::Collect()
  }
}
