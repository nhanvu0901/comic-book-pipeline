import subprocess, sys, time
FF = r"C:\Users\ADMIN\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"
root = r"D:\code\cbp-video-test-p1\repo\projects\qa_e2e"
for attempt in range(int(sys.argv[1]) if len(sys.argv) > 1 else 3):
    cmd = [FF, "-y", "-v", "warning", "-i", root + r"\video_silent.mp4", "-i", root + r"\audio_mixed.wav", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
           "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", "-shortest", r"D:\code\cbp-video-test-p1\repro_out2.mp4"]
    t = time.time(); r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"attempt {attempt}: rc {r.returncode & 0xFFFFFFFF:#x} in {time.time()-t:.1f}s;", (r.stderr or "").strip().splitlines()[-3:])
