# P1 verify — bàn giao (cbp-18, nhánh `video-qa/p1-verify`)

Ngày: 2026-10-08. Người làm: cbp-18 (tiếp nối cbp-13/agy hết quota). Reviewer: cbp-16. Integrator: cbp-12.
Evidence: `todo/video-clip-feature/p1-verify-evidence/` (log, ảnh, harness) — file nặng (final.mp4, section, preview) ở `/tmp/p1_evidence/` trên Mac và `D:\code\cbp-video-test-p1\` trên server.

## 1. Kết quả một dòng

Toàn bộ BLOCKER/MAJOR của cbp-13 + "Rescued WIP" đã đóng; E2E trên Windows (flag ON) chạy hết luồng
chọn clip → render `final.mp4`; **cờ OFF giống 6788212 từng byte (shots + video_silent) trên project thật**;
full suite: Windows **2456 passed / 14 skipped / 0 failed**, Mac **2454 passed / 15 skipped / 1 failed** (xem §6).

## 2. Đã sửa gì (theo findings-cycle1)

| Finding | Cách đóng | Test chốt |
|---|---|---|
| B2 start/bump không tồn tại, status 2 đường | `background_tts.start_background_tts / bump_priority_beat / request_resync` (registry 1 runner/project); **1 đường status duy nhất** `review/tts_status.json` qua `stages/stage_4/tts_status.py` (`cache/tts/status.json` bị xoá) | `tests/test_chatterbox_deterministic.py` (worker giả chạy qua subprocess thật) |
| **Phát hiện thêm (E2E)**: narration thật có `visual_beats` là **chuỗi**, key review là `"<sid>:<frag 0-based>"`/`intro`/`outro`; code cũ gọi `b.get("beat_id")` và key 1-based → **crash trong thread, không có duration nào** (fixture toàn dict nên không bắt được) | `beat_timing.beat_rows()` dùng chung `shots._beat_rows_for_custom` (nguồn key duy nhất); duration lấy từ **word-stream** y như Stage 4/5 | `tests/test_beat_timing.py::test_beat_durations_match_the_shot_durations_stage5_cuts` so với shot Stage 5 thật (m2) |
| B3 pick không tải, magic 3.0s, manifest "tạm" trỏ file chưa có | `/api/pick_moment` = job nền: chờ TTS cho **duration thật** (bump beat), tải section, dựng preview qua **cùng `fit`**, **rồi mới** ghi `clips.json` (start=0, open-ended); `ClipTooShort` bị từ chối kèm lý do; `/api/pick_status`, `/api/clip_preview` | `tests/test_moments_web_review.py` |
| M1 fit/hold/frozen_tail | `clips.fit(span, avail, dur)` thuần → `ClipFit(used, speed, hold)`; `plan_clip_shot()` dùng chung cho renderer **và** `frozen_tail_seconds` (kể cả speed); tail-pad để không thiếu frame; preview đi qua `render_clip_shot` | `tests/test_clip_fit_math.py` (bảng tuple, sweep bất biến, sweep số frame, freezedetect) |
| (mới) hard-cut gate | nhánh `has_clips` trong `_assemble_video` chỉ khi flag ON (flag OFF + `clips.json` cũ vẫn dissolve như xưa) | test frame-level: frame cạnh biên là màu thuần |
| M2 chunking | `plan_chunks` = `_chunks(_normalize_for_tts(toàn bộ narration))` = đúng `synthesize_project` | `test_chunks_equal_stage4s_on_a_narration_with_dashes_and_ellipses` |
| M3 reload model mỗi chunk | `synthesize_missing()` = **1 worker/1 batch**, đọc tiến độ từng dòng, cache từng chunk; seed **theo từng chunk** (kết quả chỉ phụ thuộc cache key, không phụ thuộc batch); bump giữa chừng → dừng + xếp lại; chunk lỗi thử lại 1 lần rồi báo `status.error` | `test_runner_makes_one_worker_job_per_batch…`, `test_a_bump_while_a_batch_runs_restarts_it_reordered` |
| M4/M5/M6/M7 | threadpool, không video giả, YT.Player+`getCurrentTime()`, project/beat/video_id/start được validate, URL dựng lại từ id (không fetch URL client gửi); init YT API không còn race | `test_moments_web_review.py`, `test_fastapi_launcher.py` |
| M8 | `clear_project_locks_and_clips()` là hàm thật (locks + clips + un-approve + TTS resync); test **dựng cả màn review headless** và replay handler Save/Drop/Rollback; lưu mà không đổi chữ thì **không** xoá | `tests/test_review_gate_clip_ui.py` |
| M11 | baseline **theo platform**, mỗi entry **chạy 6788212 trên chính platform đó** (`scripts/p0_baseline_capture.py`, `tests/p0_scenarios.py`: plain, whip thật sự có whip (m3), legacy clip); key = `sys.platform|ffmpeg|x264` | `tests/test_p0_baseline.py` (darwin + win32) |
| M1/m5 launcher | test dispatch vào **chính app `_run()` dựng ra**, Mount("/") cuối cùng | `test_fastapi_launcher.py` |
| m2, m5, m6, m7 | xem trên; listener keyed theo `(page, project)` (không chất đống), listener lỗi bị bỏ; pool 8 + cache theo (beat, q) | như trên |
| m4 | **không làm**: có ≥1 clip thì cả video không có whip (ghi nhận, thiết kế) | — |

Sửa thêm vì E2E lộ ra: tải section có **timeout thật + kill cả cây tiến trình** và fallback tải cả video rồi cắt cùng cửa sổ
(`clip_fetch._run_capture`); lỗi nói dòng `ERROR` thật + gợi ý yt-dlp cũ; clip shot **không mang colour tag** của nguồn
(`setparams=unknown`, chỉ nhánh ON); pick/rollback sau Approve → **rút approval** (cùng quy tắc P3).

## 3. Windows E2E (flag ON) — project copy `qa_e2e`, server `localhost:8560` qua `ssh -L`

**Không có project explore_answer nào trên server** (chỉ 2 project recap thật). Dùng bản COPY của
`logans_winter_soldier_…` (4 scene, 11 beat, audio/pages/locks thật), chuyển ở mức dữ liệu:
`narration.mode="explore_answer"`, `comic_context.plot_source="answer_research"`.

1. **TTS nền, duration hiện dần** (`logs/tts_progress.log`, `screens/tts_progress_*`): `chunks 0/4 → 1/4 (scene bump 2:0,2:1,2:2 xong trước) → 2/4 → 3/4 → 4/4`;
   chip duration trên card đổi `…s → 2.2s/3.0s → …` mà **không reload**; **1 worker job** (1 launcher + 1 python con); xoá cache rồi chạy lại cho **đúng các duration** (deterministic nhờ seed/chunk).
2. **2 beat qua UI thật**: `Dùng MP4` → tab mới → `YT.Player.getCurrentTime()` (17.12s, gửi `start=17.4`) → section → preview 9:16 (**3.80s = đúng duration beat**) → card hiện `MP4`; beat 2 (`3:0`) tương tự (3.40s). Section đã tải: 2.9 MB cho cửa sổ 6.75s (`start + beat×1.25 + 2s`).
3. Stage 4 **dùng lại cache** (26.52s, không synth); Stage 5 → `final.mp4` 26.52s. `logs/analysis_run1.txt`: **19/19** kiểm tra đo trên frame đã decode:
   clip shot đúng contract (h264 yuv420p 1080×1920 30fps, đúng số frame, muted, không colour tag); clip phát **nguyên văn** (PSNR 99 dB, 114/102 frame);
   **HARD CUT** hai phía (đúng một bước nhảy 78–92 giữa hai frame liền kề; control panel→panel là dissolve ~8 frame); preview = shot (PSNR 32–36 dB);
   mọi cut nằm trong **0.85 frame (28 ms)** so với biên beat của audio cache (`beat_windows`); audio `final.mp4` lệch **0 ms** so với `audio.wav`.
4. **Rollback** qua UI (`rollback_*` ảnh): bỏ clip `2:1` → manifest chỉ còn `3:0`, locks còn nguyên, approval bị rút; render lại: shot 2:1 là panel, clip `3:0` vẫn hard-cut (`analysis_rollback1.txt` 13/13).
5. **Sửa narration → xoá TẤT CẢ** (`logs/resync.log`): sau Save, `locks.json` = 0 lock, `clips.json` bị xoá, un-approve; TTS re-plan **chỉ 1 chunk** (cache wav 4→5, 1 worker job, ~65s), các beat không đổi giữ nguyên duration.
   (Lưu ý Flet: click Save lần đầu chỉ blur ô nhập; lần hai mới lưu — như dùng thật.)
6. **Whip = mirror** (`logs/whip_pairs.txt`, `screens/whip_old_vs_new_midframe.png`): 5 biên whip thật của project; dải "giống hệt hàng biên" dài **940–1063 hàng** với code trước fix (sọc dọc cả khung) vs **≤151 hàng** sau fix.
7. **Cờ OFF byte-identical** (`logs/flagoff.log`): cùng 1 project (không clip), 3 lần render — HEAD flag ON, HEAD flag OFF, **code của 6788212**:
   `video_silent.mp4` sha256 `39da551a3ad0251e…` giống nhau, cả 11 shot file giống nhau, `shots.json` (chuẩn hoá đường dẫn) giống nhau. Thêm baseline theo platform (3 kịch bản) khớp trên Mac **và** Windows.
   UI cờ OFF (`screens/flagoff_review_gate.png`): không có icon MP4, không chip duration, không route clip (trả SPA Flet), không `review/tts_status.json`/`cache/`, sửa chữ + Save vẫn giữ 11 lock (hành vi cũ), `CHATTERBOX_SEED=None`.

## 4. Việc cần Master/integrator quyết (không thuộc code P1)

- **yt-dlp trong venv production (`2026.07.04`) đã cũ**: YouTube trả **HTTP 403 ngay cả khi tải thường** (không riêng section); bản `2026.08.19` chạy. Em KHÔNG đụng venv prod
  (dùng bản cài riêng qua `YTDLP_BIN` trong thư mục test). Muốn demo chạy cần `pip install -U yt-dlp` ở prod (quyết định của Master).
- Đường mạng server chậm (~0.9 MB/s) và đôi lúc đứng kết nối googlevideo → pick có thể mất 1–3 phút; có timeout 180s/900s và hiện trạng thái.
- Một lần `ffmpeg 8.1` (Gyan) **crash 0xC00000FD** ở final encode khi video trộn shot panel và shot clip (khi đó clip mang tag bt709); không tái hiện được khi chạy lại 3 lần
  (lúc đó máy đang chạy nhiều job nặng khác của các session, bộ nhớ ảo trống chỉ ~8 GB). Đã bỏ tag cho clip shot để stream không đổi tham số giữa chừng — nên theo dõi.
- Beat < `QA_MIN_SHOT_SECONDS` (1.5s) bị Stage 5 gộp vào beat kề → clip của beat đó có thể rơi về panel (shots.json ghi `fallback_reason`).
- Sai số cắt ≤ 1 frame so với audio là do Stage 5 làm tròn số frame từng shot (đường ổn định, không phải P1).

## 5. Chạy lại

`harness/`: `chrome_start.sh` + `tunnel.sh` + `ui_server_e2e.py` (chính `ui.__main__._run()` nhưng bind 127.0.0.1) + `s2_pick.py`, `s4_rollback.py`, `s5_edit.py`, `analyze_render.py`,
`flagoff_compare.py`, `whip_check2.py`. Job dài trên Windows phải khởi chạy bằng `spawn.ps1` (Win32_Process.Create) — `start /b` chết theo phiên SSH.

## 6. Test

- Windows (Python 3.14, flet 0.86.5, ffmpeg 8.1, `.env` có mặt): `2456 passed, 14 skipped` (`logs/suite_win_final.txt`).
- Mac: `2454 passed, 15 skipped, 1 failed` — `tests/test_batcave_issue_verifier.py::test_batcave_verifier_live_network` (P2, gọi mạng thật; pass khi chạy riêng).
