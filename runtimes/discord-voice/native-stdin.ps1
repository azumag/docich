function Invoke-CommandWithStandardInput {
  param(
    [Parameter(Mandatory)]
    [string]$CommandLine,
    [Parameter(Mandatory)]
    [string]$Text
  )

  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $env:ComSpec
  $startInfo.Arguments = "/d /s /c `"$CommandLine`""
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardInput = $true
  $startInfo.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  $processStarted = $false
  try {
    if (-not $process.Start()) {
      throw "Failed to start the local Cloudflare CLI."
    }
    $processStarted = $true
    $process.StandardInput.Write($Text)
    $process.StandardInput.Close()
    $process.WaitForExit()
    return $process.ExitCode
  }
  finally {
    if ($processStarted -and -not $process.HasExited) {
      $process.StandardInput.Close()
      $process.Kill()
      $process.WaitForExit()
    }
    $process.Dispose()
  }
}
