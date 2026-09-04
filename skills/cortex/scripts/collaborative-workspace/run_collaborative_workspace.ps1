$ErrorActionPreference = "Stop"
$scripts = [IO.Path]::GetDirectoryName($PSCommandPath)
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$candidates = [Collections.Generic.List[string]]::new()
function Add-Candidate([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try { $full = [IO.Path]::GetFullPath($Path); $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop } catch { return }
    if ($item.PSIsContainer -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { return }
    if ($seen.Add($full)) { $candidates.Add($full) }
}
Add-Candidate $env:CORTEX_PYTHON
if ($env:VIRTUAL_ENV) { Add-Candidate (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe') }
if ($env:CONDA_PREFIX) { Add-Candidate (Join-Path $env:CONDA_PREFIX 'python.exe') }
Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { Add-Candidate $_.Source }
$registryRoots = @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore')
foreach ($root in $registryRoots) {
    Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue | Sort-Object PSChildName | ForEach-Object {
        try { Add-Candidate ((Get-ItemProperty -LiteralPath (Join-Path $_.PSPath 'InstallPath') -ErrorAction Stop).ExecutablePath) } catch {}
    }
}
if ($env:LOCALAPPDATA) { Add-Candidate (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe') }
$probe = "import json,sys,unicodedata;print(json.dumps({'python':list(sys.version_info[:2]),'ucd':unicodedata.unidata_version,'isolated':bool(sys.flags.isolated),'bytecode':bool(sys.dont_write_bytecode)},sort_keys=True,separators=(',',':')))"
$selected = $null; $selectedIndex = -1
for ($index = 0; $index -lt $candidates.Count; $index++) {
    $path = $candidates[$index]
    try {
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $path; $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
        $start.CreateNoWindow = $true; $start.Arguments = '-I -B -c "' + $probe + '"'
        $process = [Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit(5000)) { $process.Kill(); $process.WaitForExit(); continue }
        $value = $process.StandardOutput.ReadToEnd().TrimEnd("`r", "`n")
        $errorText = $process.StandardError.ReadToEnd()
    } catch { continue }
    if ($process.ExitCode -eq 0 -and -not $errorText -and $value -eq '{"bytecode":true,"isolated":true,"python":[3,11],"ucd":"14.0.0"}') { $selected = $path; $selectedIndex = $index; break }
}
if (-not $selected) { [Console]::Error.WriteLine('cortex collaborative workspace runtime error: no_compatible_python'); exit 70 }
$ordered = @($selected)
for ($index = $selectedIndex + 1; $index -lt $candidates.Count; $index++) { $ordered += $candidates[$index] }
$payload = @{schema_version=1;candidates=$ordered;probed=0;end=$ordered.Count} | ConvertTo-Json -Compress
$payload | & $selected -I -B (Join-Path $scripts 'select_collaborative_workspace.py') (Join-Path $scripts 'run_collaborative_workspace.py') @args
exit $LASTEXITCODE
