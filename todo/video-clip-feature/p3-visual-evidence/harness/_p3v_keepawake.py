# keeps Windows awake while a long job runs (ES_CONTINUOUS|ES_SYSTEM_REQUIRED); exits after N minutes
import ctypes, sys, time
mins = float(sys.argv[1]) if len(sys.argv) > 1 else 60
ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
time.sleep(mins * 60)
ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
