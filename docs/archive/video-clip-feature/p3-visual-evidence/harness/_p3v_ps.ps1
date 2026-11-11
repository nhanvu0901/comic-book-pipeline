Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -or $_.Name -like 'cmd*' } | ForEach-Object { "{0} {1} {2}" -f $_.ProcessId, $_.Name, $_.CommandLine }
