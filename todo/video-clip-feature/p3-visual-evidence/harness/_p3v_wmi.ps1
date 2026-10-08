param([string]$Cmd)
# WMI-created processes live outside the SSH session's job object, so they survive the disconnect.
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $Cmd; CurrentDirectory = 'D:\code\cbp-video-test-p3v' }
"created pid=$($r.ProcessId) rc=$($r.ReturnValue)"
