param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8002
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
foreach ($required in @('main.py', 'requirements.txt', 'diploma.db', 'meta_launcher.py')) {
    if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot $required) -PathType Leaf)) {
        throw "Missing $required. Place start.ps1 inside Deplomka."
    }
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython -m venv .venv
    } else {
        & py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment.' }
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$interfaces = @(Get-NetIPInterface -AddressFamily IPv4 -ConnectionState Connected)
$rankedRoutes = foreach ($route in (Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0')) {
    $routeInterface = $interfaces | Where-Object { $_.InterfaceIndex -eq $route.InterfaceIndex } | Select-Object -First 1
    if ($routeInterface) {
        [PSCustomObject]@{InterfaceIndex=$route.InterfaceIndex; Metric=($route.RouteMetric + $routeInterface.InterfaceMetric)}
    }
}
$lanIp = $null
foreach ($route in ($rankedRoutes | Sort-Object Metric,InterfaceIndex)) {
    $address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex |
        Where-Object { $_.AddressState -eq 'Preferred' -and !$_.SkipAsSource -and $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1
    if ($address) { $lanIp = $address.IPAddress; break }
}
if (!$lanIp) { $lanIp = '127.0.0.1' }
Write-Host ''
Write-Host 'DEPLOMKA (META)'
Write-Host "Local:   http://127.0.0.1:${Port}/"
Write-Host "Network: http://${lanIp}:${Port}/"
Write-Host 'Stop the server with Ctrl+C.'
Write-Host ''
& $python (Join-Path $PSScriptRoot 'meta_launcher.py') $lanIp $Port
exit $LASTEXITCODE
