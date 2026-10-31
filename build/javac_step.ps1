$ErrorActionPreference = "Continue"
$root = "C:\Users\28928\WorkBuddy\2026-10-11-15-32-41\diandi-gaosi"
$shell = Join-Path $root "app"
$build = Join-Path $root "build"
$sdk = "C:\Users\28928\AppData\Local\Android\Sdk"
$androidJar = "$sdk\platforms\android-36.1\android.jar"
$javaHome = "C:\Program Files\Eclipse Adoptium\jdk-17.0.4.8-hotspot"
$env:PATH = "$javaHome\bin;" + $env:PATH

$javaFiles = @()
Get-ChildItem (Join-Path $shell "src") -Recurse -Filter *.java | ForEach-Object { $javaFiles += $_.FullName }
Get-ChildItem (Join-Path $build "gen") -Recurse -Filter *.java | ForEach-Object { $javaFiles += $_.FullName }

# javac @argfile 里反斜杠是转义符，必须换成正斜杠
function Fwd($p) { return ($p -replace '\\', '/') }

$rsp = Join-Path $build "javac_args.txt"
$lines = @()
$lines += "--release"
$lines += "8"
$lines += "-encoding"
$lines += "UTF-8"
$lines += "-nowarn"
$lines += "-classpath"
$lines += '"' + (Fwd $androidJar) + '"'
$lines += "-d"
$lines += '"' + (Fwd (Join-Path $build "classes")) + '"'
foreach ($f in $javaFiles) { $lines += '"' + (Fwd $f) + '"' }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($rsp, $lines, $utf8NoBom)

$log = Join-Path $build "javac.log"
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Join-Path $javaHome "bin\javac.exe")
$psi.Arguments = '@"' + (Fwd $rsp) + '"'
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)
$stdout = $proc.StandardOutput.ReadToEnd()
$stderr = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

$report = "javaFiles=" + $javaFiles.Count + "`r`nexit=" + $proc.ExitCode + "`r`n--- stdout ---`r`n" +
    $stdout + "`r`n--- stderr ---`r`n" + $stderr
[System.IO.File]::WriteAllText($log, $report, $utf8NoBom)
Write-Output ("javac exit=" + $proc.ExitCode + "  javaFiles=" + $javaFiles.Count)
