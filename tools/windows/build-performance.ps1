# SPDX-License-Identifier: GPL-3.0-or-later
<# Reproduce the build-386 Windows PGO recipe; never launches the emulator. #>
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Preflight', 'Configure', 'Build', 'Test')]
    [string]$Action = 'Preflight',
    [string]$LlvmRoot = $env:LLVM_ROOT,
    [string]$QtRoot = $env:QT_ROOT_DIR,
    [string]$SourceDirectory = (Join-Path $PSScriptRoot '../..'),
    [string]$BuildDirectory = 'build/performance-pgo',
    [ValidateRange(1, 32)][int]$Jobs = 4,
    [switch]$Tests
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Action -eq 'Test' -and -not $Tests) { throw 'Test requires -Tests and a separate build directory' }
$source = (Resolve-Path -LiteralPath $SourceDirectory).Path
if (-not [IO.Path]::IsPathRooted($BuildDirectory)) {
    $BuildDirectory = Join-Path $source $BuildDirectory
}
$BuildDirectory = [IO.Path]::GetFullPath($BuildDirectory)
$profile = Join-Path $source 'lichii-windows.profdata'
$profileHash = '07cc193d6d8faa0a9ef94a6c4a3bfa43cf2918516f9a9e430c40e145b6de20bc'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program exited with $LASTEXITCODE" }
}
function Save-Json($Value, [string]$Name) {
    New-Item -ItemType Directory -Force -Path $BuildDirectory | Out-Null
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $BuildDirectory $Name) -Encoding utf8
}

# Keep these aligned with build-386's release-binaries.yml Configure Windows step.
# Paths containing spaces must stay inside the single CMake flags argument.
$profileForCompiler = $profile.Replace('\', '/')
$flags = "-DWIN32 -D_WINDOWS -DNOMINMAX -flto=thin -fprofile-use=`"$profileForCompiler`" -Wno-error=profile-instr-out-of-date -Wno-error=profile-instr-unprofiled -Wno-error=backend-plugin"
$testValue = if ($Tests) { 'ON' } else { 'OFF' }
$configure = @(
    '-S', $source, '-B', $BuildDirectory, '-G', 'Ninja',
    '-DCMAKE_BUILD_TYPE=Release', "-DBUILD_TESTING=$testValue", "-DLICHII_TESTS=$testValue",
    '-DLICHII_CMD=ON', '-DLICHII_ROOM=OFF', '-DLICHII_ROOM_STANDALONE=OFF',
    '-DLICHII_USE_BUNDLED_QT=OFF', '-DENABLE_FLYHIGH=ON', '-DENABLE_QT_TRANSLATION=ON',
    '-DLICHII_USE_BUNDLED_SDL3=ON', '-DENABLE_UPDATE_CHECKER=ON',
    '-DUSE_DISCORD_PRESENCE=OFF', '-DUSE_CCACHE=OFF',
    '-DLICHII_BUILD_PRESET=custom', '-DLICHII_BUILD_PLATFORM=windows-x86_64-pgo',
    "-DCMAKE_PREFIX_PATH=$QtRoot", "-DCMAKE_C_COMPILER=$LlvmRoot/bin/clang-cl.exe",
    "-DCMAKE_CXX_COMPILER=$LlvmRoot/bin/clang-cl.exe", "-DCMAKE_LINKER=$LlvmRoot/bin/lld-link.exe",
    "-DCMAKE_AR=$LlvmRoot/bin/llvm-lib.exe", '-DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded',
    '-DCMAKE_EXE_LINKER_FLAGS=/DEBUG:FULL /OPT:REF /OPT:ICF',
    "-DCMAKE_C_FLAGS=$flags", "-DCMAKE_CXX_FLAGS=-EHsc $flags"
)
if ($Action -eq 'Plan') {
    [ordered]@{ program = 'cmake'; arguments = $configure; jobs = $Jobs; tests = [bool]$Tests } |
        ConvertTo-Json -Depth 5
    exit 0
}

$problems = [Collections.Generic.List[string]]::new()
$versions = [ordered]@{}
foreach ($tool in @('git', 'cmake', 'ninja', 'rc.exe', 'cl.exe')) {
    $command = Get-Command $tool -ErrorAction SilentlyContinue
    if ($command) { $versions[$tool] = $command.Source }
    else { $problems.Add("$tool missing from PATH (load the x64 Visual Studio development environment)") }
}
if (Get-Command cmake -ErrorAction SilentlyContinue) {
    $versions['cmake_version'] = (& cmake --version | Select-Object -First 1)
    if ($versions['cmake_version'] -notmatch '([0-9]+\.[0-9]+\.[0-9]+)' -or
        [version]$Matches[1] -lt [version]'3.31.0') { $problems.Add('CMake 3.31 or newer is required') }
}
foreach ($tool in @('clang-cl.exe', 'lld-link.exe', 'llvm-lib.exe', 'llvm-profdata.exe')) {
    if (-not $LlvmRoot -or -not (Test-Path -LiteralPath "$LlvmRoot/bin/$tool")) {
        $problems.Add("LLVM_ROOT must contain bin/$tool (LLVM 22.1.8)")
    }
}
if ($LlvmRoot -and (Test-Path -LiteralPath "$LlvmRoot/bin/clang-cl.exe")) {
    $versions['clang'] = (& "$LlvmRoot/bin/clang-cl.exe" --version) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $versions['clang'] -notmatch 'clang version 22\.1\.8\b') {
        $problems.Add('Expected clang-cl 22.1.8, as used by build-386')
    }
}
if (-not $env:VCToolsInstallDir -or -not $env:WindowsSdkDir) {
    $problems.Add('MSVC headers/libraries and Windows SDK environment are missing')
}
$qtConfig = "$QtRoot/lib/cmake/Qt6/Qt6ConfigVersion.cmake"
if (-not $QtRoot -or -not (Test-Path -LiteralPath $qtConfig)) {
    $problems.Add('QT_ROOT_DIR must be Qt 6.8.3 win64_msvc2022_64 with qtcharts and qtimageformats')
} elseif (-not (Test-Path -LiteralPath "$QtRoot/bin/qmake.exe")) {
    $problems.Add('Qt qmake executable is missing; cannot verify the kit version')
} else {
    $versions['qt_version'] = (& "$QtRoot/bin/qmake.exe" -query QT_VERSION) -join ''
    if ($LASTEXITCODE -ne 0 -or $versions['qt_version'].Trim() -ne '6.8.3') {
        $problems.Add('Expected Qt 6.8.3')
    }
}
foreach ($module in @('Qt6Charts', 'Qt6Svg')) {
    if (-not (Test-Path -LiteralPath "$QtRoot/lib/cmake/$module/${module}Config.cmake")) {
        $problems.Add("Qt module $module is missing")
    }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $profile).Hash.ToLowerInvariant() -ne $profileHash) {
    $problems.Add('Windows PGO profile differs from build-386; use a separate experiment')
}
$report = [ordered]@{
    schema = 1; source = $source; baseline = '00f580a94b3e368b1502727e98cdd6ad80b000cf'
    utc = [DateTime]::UtcNow.ToString('o'); versions = $versions; problems = @($problems.ToArray())
    vc_tools = $env:VCToolsVersion; windows_sdk = $env:WindowsSDKVersion
    llvm_root = $LlvmRoot; qt_root = $QtRoot; profile_sha256 = $profileHash
    configure_arguments = $configure; jobs = $Jobs; tests = [bool]$Tests
}
Save-Json $report 'preflight.json'
if ($problems.Count) { throw ($problems -join "`n") }
if ($Action -eq 'Preflight') { Write-Host 'Preflight passed'; exit 0 }

$report['revision'] = (Invoke-Checked 'git' @('-C', $source, 'rev-parse', 'HEAD'))
$report['changes'] = @(Invoke-Checked 'git' @('-C', $source, 'status', '--porcelain=v1'))
$report['compiler_sha256'] = (Get-FileHash -Algorithm SHA256 -LiteralPath "$LlvmRoot/bin/clang-cl.exe").Hash
$report['dependencies_sha256'] = (Get-FileHash -Algorithm SHA256 -LiteralPath "$source/cpmfile.json").Hash
Save-Json $report 'build-manifest.json'
Invoke-Checked 'cmake' $configure
if ($Action -eq 'Configure') { exit 0 }
$targets = if ($Tests) { @() } else { @('--target', 'lichii', 'lichii-cmd') }
Invoke-Checked 'cmake' (@('--build', $BuildDirectory, '--parallel', "$Jobs") + $targets)
if ($Tests) { Invoke-Checked 'ctest' @('--test-dir', $BuildDirectory, '--output-on-failure') }
foreach ($file in @('bin/lichii.exe', 'bin/lichii.pdb', 'bin/lichii-cmd.exe', 'CMakeCache.txt', 'compile_commands.json')) {
    $path = Join-Path $BuildDirectory $file
    if (Test-Path -LiteralPath $path) { $report[$file] = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash }
}
$report['completed_utc'] = [DateTime]::UtcNow.ToString('o')
Save-Json $report 'build-manifest.json'
