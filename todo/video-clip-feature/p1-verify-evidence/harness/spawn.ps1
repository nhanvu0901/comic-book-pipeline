param([Parameter(Mandatory=$true)][string]$CommandLine)
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $CommandLine }
"spawned pid=$($r.ProcessId) rc=$($r.ReturnValue)"
