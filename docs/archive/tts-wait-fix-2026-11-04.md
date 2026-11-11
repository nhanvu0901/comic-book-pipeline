# Clip pick waiting TTS — 2026-11-04

## Confirmed cause

Project `spider-man_wolverine_2_2025` had status running=false, completed=false, chunks_done=0/7 and Chatterbox worker OSError1455 while loading s3gen.safetensors. Windows had32GB RAM, AutomaticManagedPagefile=false and no configured/active pagefile. UI PID2964 used about12.7GB committed private memory, leaving about8GB commit headroom. The picker ignored terminal status.error and continued waiting up to900seconds.

## Code

Terra implemented commit39fd2e5 on codex/fix-tts-wait-2026-11-04 (base9fba2a9), updating only ui/web_routes.py and tests/test_moments_web_review.py. A retained runner distinguishes a new/running retry from old errors. Once runner stops, status/duration are read again; available duration wins, otherwise terminal TTS error reaches the picker instead of a timeout. It never substitutes estimated duration. Independent Terra review approved.

Root applied exact patch to local checkout and the server. Server ui/web_routes.py contained unrelated edits, so only checked hunks were applied; no full-file replacement. Backup: D:\code\cbp-deploy-backups\web_routes_before_tts_wait_20261104.py. Test backup is alongside it.

## Runtime recovery

Paused scheduled taskComicUI to free UI memory, ran existing BackgroundTTSRunner.run_sync for the actual project using unchanged narration, normal voice/config and exact cache implementation. No OS pagefile/reboot change was performed. Model loaded successfully; all7chunks cached,19beat durations and windows completed, error=null. Restarted ComicUI; listener8550 PID16656 at verification, and API /api/beat_tts_status?project=spider-man_wolverine_2_2025 returned HTTP200 and completed=true after SSH session ended. User needs refresh/reselect because previous in-memory picker job ended with the old UI process.

## Validation

- Local57 relevant tests passed in12.49seconds.
- Windows57 tests passed in36.12seconds; one pre-existing Starlette/httpx deprecation warning.
- Root asserted live API completed,7chunks,19durations,errornull after UI restart.
- No claim of a completed new video download; no replacement URL was chosen on behalf of the user.

## Remaining OS issue

Windows pagefile remains disabled. This project's cached durations are recovered, but new uncached TTS may again exhaust system commit memory. User was asked asynchronously whether to enable Windows-managed pagefile and reboot after synthesis; no approval received at completion. Reboot affects other applications, so it was not performed. The terminal-error UI fix reports such failures; it does not change memory requirements.

Diagnostic artifacts: /tmp/cbp-tts-debug-20261104/. Server retry log: D:\code\cbp-deploy-backups\retry_project_tts_20261104.log. Sources/narration were not altered by recovery.
