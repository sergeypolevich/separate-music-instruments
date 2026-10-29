$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = $null

function Test-Python312($candidate) {
    if (-not $candidate) { return $false }
    try {
        & $candidate -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) {
    try {
        $candidate = (& $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and (Test-Python312 $candidate)) { $pythonExe = $candidate.Trim() }
    } catch { }
}

if (-not $pythonExe) {
    $installDir = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'
    $pythonExe = Join-Path $installDir 'python.exe'
}

if (-not (Test-Python312 $pythonExe)) {
    Write-Host 'Python 3.12 is missing. Downloading the official Python 3.12.10 installer...'
    $installerUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
    $installer = Join-Path $env:TEMP 'audio-stems-python-3.12.10-amd64.exe'
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installer -UseBasicParsing
        $signature = Get-AuthenticodeSignature -FilePath $installer
        if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate.Subject.Contains('Python Software Foundation')) {
            throw 'The downloaded installer does not have a valid Python Software Foundation signature.'
        }
        $arguments = '/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_pip=1 Include_test=0 Include_doc=0 Include_tcltk=1 TargetDir="' + $installDir + '"'
        $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Python installation failed with exit code $($process.ExitCode)." }
        if (-not (Test-Python312 $pythonExe)) { throw 'Python installer finished, but Python 3.12 could not be verified.' }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}

# The first start puts an "Audio Stems" shortcut with the app's icon on the desktop: a .bat cannot have its own icon.
# Once only (the marker stays in app/): a shortcut the user deleted is not put back.
$shortcutMarker = Join-Path $projectRoot '.shortcut-created'
if (-not (Test-Path -LiteralPath $shortcutMarker)) {
    try {
        $startFile = Join-Path (Split-Path -Parent $projectRoot) 'Start Windows.bat'
        $desktop = [Environment]::GetFolderPath('Desktop')
        $link = Join-Path $desktop 'Audio Stems.lnk'
        if ($desktop -and (Test-Path -LiteralPath $startFile) -and -not (Test-Path -LiteralPath $link)) {
            $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
            $shortcut.TargetPath = $startFile
            $shortcut.WorkingDirectory = Split-Path -Parent $startFile
            $shortcut.IconLocation = (Join-Path $projectRoot 'icons\icon.ico') + ',0'
            $shortcut.Description = 'Audio Stems'
            $shortcut.Save()
            Write-Host "Created the desktop shortcut $link"
        }
        Set-Content -LiteralPath $shortcutMarker -Value 'created'
    } catch {
        Write-Host "Could not create the desktop shortcut: $($_.Exception.Message)"
    }
}

Write-Host 'Starting Audio Stems. First startup may download the application dependencies and Demucs runtime.'
Push-Location $projectRoot
try {
    & $pythonExe (Join-Path $projectRoot 'run.py') --app
    if ($LASTEXITCODE -ne 0) { throw "Audio Stems exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
