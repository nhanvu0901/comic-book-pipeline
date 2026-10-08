# IMPLEMENTATION PLAN: Tích Hợp Video Clips Vào Pipeline Q&A (Phân Chia Worker & Delivery Option A)

| Kế hoạch | Trạng thái | Ngày cập nhật | Phiên bản |
|---|---|---|---|
| `todo/video-clip-feature/2026-10-07-video-qa-plan.md` | Bản kế hoạch chi tiết (Master Decisions a/b, 13 Fixes & Worker Allocation) | 2026-10-08 | 2.0 |

---

## 1. Quy Trình Phân Phối (Delivery Option A) & Phân Chia Nhân Sự (Worker Allocation)

### 1.1. Phân Chia Công Việc Đa Luồng Giữa Các Worker
Dự án được phân rã cho các worker chuyên biệt cùng phối hợp trên Git repository chung:

| Worker / Session | Phạm vi phụ trách | Nhánh làm việc | Nhiệm vụ chi tiết |
|---|---|---|---|
| **`video-qa/p1-verify`** | P1 Review & Windows E2E | `video-qa/p1-verify` | Review các task P1, chạy test suite hồi quy hybrid và thực hiện kiểm thử E2E trên Windows Server. |
| **`video-qa/p3-core`** | P3 Core Infrastructure | `video-qa/p3-core` | Xây dựng các module P3 mới: CLI (`stages/screen_pipeline.py`), Research (`stages/stage_1/screen_research.py`), Narration (`stages/stage_3/screen_script.py`). |
| **`video-qa/p3-visual`** | P3 Visual & Fallbacks | `video-qa/p3-visual` | Xây dựng Shot Builder P3 mới (`stages/stage_5/screen_shots.py`), chuỗi Fallback 4 cấp độ không crash, giao diện Screen UI. |
| **`comic-book-pipeline-12`** | P2 & Integrator | `feat/video-qa-hybrid` | Hoàn thiện P2 (Batcave issue verifier, Router rules) và đóng vai trò **Integrator**: merge các nhánh worker, giải quyết conflict, chạy full suite và quản lý deploy. |

### 1.2. Quy Trình Phân Phối Gated Delivery (Option A)
1. Các worker phát triển trên nhánh riêng (tách từ `feat/video-qa-hybrid@5c60eef`).
2. Integrator (`comic-book-pipeline-12`) merge các nhánh vào `feat/video-qa-hybrid`, giải quyết xung đột, chạy kiểm thử toàn bộ.
3. Chạy test trên thư mục riêng trên Windows Server qua SSH tunnel: `D:\code\cbp-video-test-int` (cổng `8562`).
4. Push nhánh `feat/video-qa-hybrid`.
5. **DỪNG LẠI CHỜ PHÊ DUYỆT CỦA MASTER (Gated Checkpoint)**.
6. **CHỈ SAU KHI MASTER ĐỒNG Ý RÕ RÀNG**: Pull code vào thư mục production `D:\code\comic-book-pipeline`, khởi động lại UI trên cổng `8550` với flag `ENABLE_VIDEO_CLIPS=1` để demo cho Master. Tuyệt đối không can thiệp thư mục production hay firewall trước khi có lệnh.

---

## 2. Giai Đoạn P0: Sửa Lỗi Whip Bridge & Thu Thập Baseline Byte-Identical

Mục tiêu: Sửa dứt điểm lỗi vệt sọc đứng bằng phản chiếu gương (mirroring) và chốt baseline byte-identity.

### Task 0.1: Thiết Lập Nhánh Tích Hợp & Môi Trường Thử Nghiệm
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator (`comic-book-pipeline-12`).
- **Files to touch**: Git repository branches.
- **Nội dung thực hiện**: Tạo nhánh tích hợp `feat/video-qa-hybrid` từ mốc `ao/comic-book-pipeline-3/root`. Setup checkout phụ tại `D:\code\cbp-video-test` trên Windows Server.
- **Tiêu chí nghiệm thu**: 50 tests hiện tại của `tests/test_video_clips.py` pass 100%.

### Task 0.2: Sửa Lỗi Vệt Sọc Đứng Bằng Phản Chiếu Mirroring (Fix 6)
- **Phân loại**: TOUCHES STABLE PATH (`stages/stage_5/pipeline.py:706-720`).
- **Guard**: Tổng thời lượng bridge và mốc audio mượn của cảnh kề giữ nguyên (`pipeline.py:631-649`).
- **Worker**: Integrator.
- **Files to touch**: `stages/stage_5/pipeline.py` (Dòng 706–720).
- **Nội dung thực hiện**:
  - **TUYỆT ĐỐI KHÔNG DÙNG STRETCHING**: Bỏ toàn bộ việc kéo giãn 1 hàng pixel đáy.
  - Lấp khoảng trống `px` bằng cách trích xuất dải biên đa hàng và lật đối xứng gương (**MIRRORING / REFLECT**) qua trục ngang (`Image.FLIP_TOP_BOTTOM`).
  - Sửa đối xứng cho cả `_shift_up` và `_shift_from_below`.
- **Tests written FIRST (TDD)**:
  - `tests/test_whip_bridge.py`:
    - `test_shift_up_no_vertical_streaks()`: Kiểm tra không có dải pixel bị kéo giãn hoặc lặp lại 1D đơn điệu.
    - `test_shift_from_below_no_vertical_streaks()`: Kiểm tra tương tự cho dải đỉnh.
    - `test_whip_bridge_duration_and_audio_sync_preserved()`: Đảm bảo tổng thời lượng không đổi.
- **Tiêu chí nghiệm thu**: 100% tests pass, video render đoạn chuyển tiếp không còn sọc đứng.

### Task 0.3: Thu Thập Baseline Byte-Identity Ngay Sau Whip Fix (Fix 7)
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `tests/test_p0_baseline.py` (mới), `tests/fixtures/p0_baseline.json` (mới).
- **Nội dung thực hiện**:
  - Chạy fixture render chuẩn ngay sau khi Task 0.2 hoàn thành.
  - Tính toán và lưu hash SHA256 của `shots.json` và `video_silent.mp4` vào `tests/fixtures/p0_baseline.json`.
  - Do cơ chế chọn whip transition được seed tất định theo `f"{project}:{scene_a}:{scene_b}"` (`pipeline.py:609-623`), baseline này có tính tất định 100% và đóng vai trò làm thước đo hồi quy cho P1.
- **Tests written FIRST (TDD)**:
  - `tests/test_p0_baseline.py`:
    - `test_p0_baseline_matches_stored_hash()`: Assert hash render khớp chính xác với baseline đã lưu.
- **Tiêu chí nghiệm thu**: File baseline được ghi nhận và pass test kiểm tra lặp lại.

---

## 3. Giai Đoạn P1: Lớp Video Clip Hybrid Trong Comic Q&A

Mục tiêu: Tích hợp video clips vào Comic Q&A, giữ comic làm nguồn gốc chính, bảo toàn byte-identical khi flag OFF.

### Task 1.1: Feature Flags, Post-Atempo Giữ Nguyên & Conditional Seeding (Fix 1 & 2)
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `config.py`, `.env.example`.
- **Nội dung thực hiện**:
  - Khai báo cờ `ENABLE_VIDEO_CLIPS = bool(int(os.getenv("ENABLE_VIDEO_CLIPS", "0")))`.
  - Khai báo các thông số fit: `CLIP_SPEED_MIN = 0.8`, `CLIP_SPEED_MAX = 1.25`, `CLIP_MAX_HOLD = 0.3`.
  - **Fix 1**: **KHÔNG ĐƯỢC ĐỔI MẶC ĐỊNH `POST_ATEMPO` TRONG `config.py`** (giữ nguyên mặc định 1.30 trong code). Đọc giá trị hiệu dụng từ `.env` (chỉ rõ 1.15).
  - **Fix 2**: `CHATTERBOX_SEED` **CHỈ ĐƯỢC SEED KHI FLAG ON** (`42` nếu bật). Khi flag OFF, giữ nguyên unseeded TTS (`CHATTERBOX_SEED = None`, nhiệt độ ngẫu nhiên 0.8 như cũ).
- **Tests written FIRST (TDD)**:
  - `tests/test_qa_clip_config.py`:
    - `test_default_flags_off_unseeded()`: Kiểm tra khi flag OFF thì `CHATTERBOX_SEED is None` và `POST_ATEMPO` code default là 1.30.
    - `test_flags_on_seeded()`: Kiểm tra khi flag ON thì `CHATTERBOX_SEED == 42`.
- **Tiêu chí nghiệm thu**: Mọi cấu hình chuẩn xác theo đúng quy tắc flag ON/OFF.

### Task 1.2: FastAPI Launcher Chỉ Bọc Khi Flag ON (Fix 3)
- **Phân loại**: TOUCHES STABLE PATH (`ui/__main__.py:87`).
- **Guard**: Khi flag OFF, sử dụng 100% đường chạy cũ.
- **Worker**: Integrator / `video-qa/p1-verify`.
- **Files to touch**: `ui/__main__.py`, `ui/web_routes.py` (mới).
- **Nội dung thực hiện**:
  - **Fix 3**: Trong hàm `_run()` tại `ui/__main__.py:87`:
    - Nếu `not config.ENABLE_VIDEO_CLIPS`: Chạy chính xác lệnh cũ `ft.run(main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=args.port, assets_dir=str(PROJECTS_ROOT))`. Hoàn toàn không nạp FastAPI.
    - Nếu `config.ENABLE_VIDEO_CLIPS`: Mới xuất `flet_asgi`, tạo FastAPI app, nạp `clip_router` trước rồi mount `flet_asgi` sau cùng, chạy qua Uvicorn trên cổng `8550`.
- **Tests written FIRST (TDD)**:
  - `tests/test_fastapi_launcher.py`:
    - `test_lan_uses_exact_old_ft_run_when_flag_off()`: Đảm bảo không import/wrap FastAPI khi flag OFF.
    - `test_lan_uses_fastapi_wrap_when_flag_on()`: Đảm bảo khởi tạo FastAPI và route clip ưu tiên trước Flet khi flag ON.
- **Tiêu chí nghiệm thu**: Flag OFF chạy Flet thuần không phụ thuộc FastAPI; flag ON mở web routes bình thường.

### Task 1.3: Background TTS Runner, Worker Seeding & Không Gọi `ensure_reviewed` (Fix 8)
- **Phân loại**: TOUCHES STABLE PATH (`stages/stage_4/_chatterbox_worker.py:27`, `stage_4/pipeline.py:151`).
- **Worker**: Integrator.
- **Files to touch**: `stages/stage_4/_chatterbox_worker.py`, `stages/stage_4/background_tts.py` (mới), `stages/stage_4/pipeline.py`.
- **Nội dung thực hiện**:
  - Tại `stages/stage_4/_chatterbox_worker.py`: Gán seed cố định (`torch.manual_seed`, `np.random.seed`, `random.seed`) khi nhận `job.get("seed")`.
  - Tạo `stages/stage_4/background_tts.py`: Quản lý tiến trình tổng hợp nền. **TUYỆT ĐỐI KHÔNG GỌI `ensure_reviewed()`** vì review gate đang mở và cố tình chặn Stage 4.
  - Lưu file WAV vào `projects/<p>/cache/tts/<sha256>.wav`.
  - Tại `stages/stage_4/pipeline.py:151`: Tái sử dụng các chunk đã cache, chỉ synthesize các chunk còn thiếu. Dùng chính xác thuật toán chia câu `_chunks`, `_even_words` và atempo.
- **Tests written FIRST (TDD)**:
  - `tests/test_chatterbox_deterministic.py`:
    - `test_background_tts_does_not_call_ensure_reviewed()`: Mock `ensure_reviewed`, đảm bảo không bị gọi trong background job.
    - `test_stage_4_reuses_cached_chunks()`: Kiểm tra Stage 4 nạp cache thành công mà không gọi worker cho chunk đã có.
- **Tiêu chí nghiệm thu**: Background TTS chạy êm ái mà không bị chặn bởi Review Gate; Stage 4 tái sử dụng 100% cache.

### Task 1.4: Bộ Tính Beat Window & Keep-Awake Windows
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `stages/stage_4/beat_timing.py` (mới).
- **Nội dung thực hiện**:
  - Xây dựng `calculate_beat_durations` phân bổ thời lượng câu sang beat theo `_even_words`, kết hợp gap absorption, gộp beat $< 1.5\text{s}$ và khấu trừ $0.12\text{s}$ whip transition.
  - Tích hợp `SetThreadExecutionState(0x80000002)` chống sleep trên Windows.
- **Tests written FIRST (TDD)**:
  - `tests/test_beat_timing.py`:
    - `test_beat_window_calculation_accuracy()`: Kiểm tra khớp timing với Stage 5.
- **Tiêu chí nghiệm thu**: Thời lượng beat tính toán khớp hoàn toàn với shot render thực tế.

### Task 1.5: UI Review Gate: Nút MP4 & Reset Toàn Bộ Project Khi Sửa Narration (Master Decision a & Fix 5)
- **Phân loại**: TOUCHES STABLE PATH (`ui/screens/s_review_gate.py`).
- **Guard**: Chỉ kích hoạt khi `config.ENABLE_VIDEO_CLIPS=True`.
- **Worker**: Integrator / `video-qa/p1-verify`.
- **Files to touch**: `ui/screens/s_review_gate.py`.
- **Nội dung thực hiện**:
  - Thêm icon nút "Dùng MP4" trên từng beat card, mở tab `/moments_review` qua `page.launch_url`.
  - Cập nhật card sang trạng thái MP4 qua pubsub khi nhận event chọn clip.
  - **Master Decision a & Fix 5**: Hàm `_clear_all_locks_and_clips()`: Khi người dùng sửa text narration (hoặc thêm/xóa/gộp fragment), **TỰ ĐỘNG XÓA SẠCH TOÀN BỘ (ALL) panel locks VÀ MP4 selections của TOÀN BỘ PROJECT** (xóa sạch `locks.json` và `clips.json`). Background TTS kích hoạt lại và chỉ re-synthesize các câu bị thay đổi.
- **Tests written FIRST (TDD)**:
  - `tests/test_review_gate_clip_ui.py`:
    - `test_narration_edit_clears_all_project_locks_and_clips()`: Mô phỏng sửa 1 câu, xác minh toàn bộ locks và clips của cả project bị reset.
- **Tiêu chí nghiệm thu**: Xóa sạch đúng toàn bộ lựa chọn theo lệnh Master; đồng bộ thời gian thực mượt mà.

### Task 1.6: Web App Tab `/moments_review` (8 Luồng Song Song)
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `ui/web_routes.py`, `stages/clip_fetch.py`.
- **Nội dung thực hiện**:
  - Xây dựng HTML tab `/moments_review` nhúng YouTube IFrame API (`enablejsapi=1`), gợi ý mốc thời gian, link xem trực tiếp trên YouTube và nút "Dùng từ đây" POST về `/api/pick_moment`.
  - Chạy `moment_search` song song 8 luồng, cache kết quả theo `(beat, query)`.
- **Tests written FIRST (TDD)**:
  - `tests/test_fastapi_launcher.py`: Kiểm tra route `/moments_review` và endpoint `/api/pick_moment`.
- **Tiêu chí nghiệm thu**: Giao diện tab phản hồi nhanh dưới 15s, gửi dữ liệu chính xác về Flet.

### Task 1.7: Tải Section Với Công Thức Biên Chính Xác (Fix 9)
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `stages/clip_fetch.py`, `stages/stage_5/clips.py`.
- **Nội dung thực hiện**:
  - Viết `ytdlp_section_args` và `fetch_clip_section`: Tính điểm kết thúc chính xác:
    `end = start + beat_duration * config.CLIP_SPEED_MAX + margin` (với `margin = 2.0s`).
  - Viết `render_clip_preview` trong `stages/stage_5/clips.py` dựng video preview 9:16 đúng độ dài beat.
- **Tests written FIRST (TDD)**:
  - `tests/test_download_section.py`:
    - `test_ytdlp_section_args_calculation()`: Kiểm tra công thức end time có tính `CLIP_SPEED_MAX`.
    - `test_preview_rendered_to_contract()`: Kiểm tra video preview 9:16.
- **Tiêu chí nghiệm thu**: File tải nhỏ hơn $14–33\times$, preview chuẩn 9:16.

### Task 1.8: Triển Khai Fit Math & Hard Cut Tại Render Stage 5 (Fix 10)
- **Phân loại**: TOUCHES STABLE PATH (`stages/stage_5/clips.py`, `stages/stage_5/pipeline.py`).
- **Guard**: Chỉ chạy khi shot có `clip_path`.
- **Worker**: Integrator.
- **Files to touch**: `stages/stage_5/clips.py`, `stages/stage_5/pipeline.py`.
- **Nội dung thực hiện**:
  - Trong `render_clip_shot`: Gọi `_fit_clip_timing` co giãn tốc độ trong dải `[CLIP_SPEED_MIN, CLIP_SPEED_MAX]` (0.8 - 1.25x), giữ frame cuối $\le 0.3\text{s}$ (`CLIP_MAX_HOLD`).
  - Trong `pipeline.py`: Ép **HARD CUT** quanh shot có clip; panel comic giữ nguyên hòa tan dissolve (`XFADE_DURATION=0.25`).
- **Tests written FIRST (TDD)**:
  - `tests/test_clip_fit_math.py`:
    - `test_fit_timing_speed_bounds()`: Kiểm tra hệ số tốc độ trong ngưỡng cho phép.
    - `test_hard_cuts_enforced_for_clips()`: Kiểm tra concat graph ép cut cứng quanh clip.
- **Tiêu chí nghiệm thu**: Video render mượt mà, khớp hoàn hảo với giọng đọc narration.

### Task 1.9: Kiểm Thử TDD Hồi Quy Toàn Diện P1
- **Phân loại**: ADDITIVE.
- **Worker**: `video-qa/p1-verify`.
- **Files to touch**: `tests/test_video_qa_hybrid.py` (mới).
- **Nội dung thực hiện**:
  - Viết test kiểm tra tính toàn vẹn byte-identical so với `p0_baseline.json` khi flag `ENABLE_VIDEO_CLIPS=0`.
  - Kiểm tra hợp đồng clip shot (1080x1920 30fps no audio) và cơ chế rollback.
- **Tiêu chí nghiệm thu**: 100% test pass.

### Task 1.10: Kiểm Thử Windows E2E & Checkpoint P1
- **Worker**: `video-qa/p1-verify`.
- **Nội dung thực hiện**: Chạy test suite và render mẫu project trên `D:\code\cbp-video-test` qua SSH tunnel.
- **Checkpoint P1**: Báo cáo video mẫu cho Master.

---

## 4. Giai Đoạn P2: Type-Safe Media Source Router

Mục tiêu: Phân loại câu hỏi Q&A tự động, type-safe, có kiểm tra sâu cấp issue Batcave.

### Task 2.1: Schema Pydantic Cho Router
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator (`comic-book-pipeline-12`).
- **Files to touch**: `stages/research_scout/router_schema.py` (mới).
- **Nội dung thực hiện**: Định nghĩa `PrimaryMedium`, `VisualSource`, `RoutedItem`, `QuestionRouteResponse`.
- **Tests written FIRST (TDD)**: `tests/test_router_schema.py`.
- **Tiêu chí nghiệm thu**: Schema serialize/deserialize 100% hợp lệ.

### Task 2.2: LLM Router Client (Gemini 2.5 Flash Lite)
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `stages/research_scout/media_router.py` (mới).
- **Nội dung thực hiện**: Gọi Gemini 2.5 Flash Lite với structured JSON output, retry tối đa 2 lần.
- **Tests written FIRST (TDD)**: `tests/test_media_router_client.py`.
- **Tiêu chí nghiệm thu**: Tỷ lệ lỗi schema 0%, độ trễ $\le 2.5\text{s}$.

### Task 2.3: Batcave Issue Deep Verifier (4 Bước)
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `stages/stage_1/batcave_verifier.py` (mới).
- **Nội dung thực hiện**: Triển khai kiểm tra sâu 4 bước (disambiguation năm $\rightarrow$ chapters $\rightarrow$ issue match $\rightarrow$ getChapterData ping).
- **Tests written FIRST (TDD)**: `tests/test_batcave_issue_verifier.py`.
- **Tiêu chí nghiệm thu**: Phân biệt chuẩn xác ASM 1963 #121 (True) vs ASM 2018 #121 (False).

### Task 2.4: Deterministic Rules & Safety Net
- **Phân loại**: ADDITIVE.
- **Worker**: Integrator.
- **Files to touch**: `stages/research_scout/router_rules.py` (mới).
- **Nội dung thực hiện**: Luật xác định: `screen_qa` khi và chỉ khi không có comic/mixed AND Batcave issue fail AND clips found. Ngược lại `comic_qa`.
- **Tests written FIRST (TDD)**: `tests/test_router_rules.py`.
- **Tiêu chí nghiệm thu**: Đạt 10/10 trên testset đối chiếu, Civil War giữ đúng route `comic_qa`.

### Task 2.5: Kiểm Thử Windows & Checkpoint P2
- **Worker**: Integrator.
- **Nội dung thực hiện**: Chạy test router trên server phụ, xác nhận kết nối mạng You.com và Batcave.

---

## 5. Giai Đoạn P3: Chế Độ Độc Lập `screen_qa` (NEW MODULES ONLY - Fix 4 & 11)

Mục tiêu: Xây dựng chế độ thuần video cho phim/hoạt hình, hoàn toàn trong các module mới, không sửa code cũ, không bao giờ crash.

### Task 3.1: Đăng Ký Mode Mới & CLI Entrypoint Mới (Fix 4)
- **Phân loại**: ADDITIVE.
- **Worker**: `video-qa/p3-core`.
- **Files to touch**: `config.py`, `stages/screen_pipeline.py` (mới).
- **Nội dung thực hiện**:
  - Đăng ký `SCREEN_QA = "screen_qa"`.
  - Tạo CLI mới `stages/screen_pipeline.py` (tương tự `answer_pipeline.py`), điều phối toàn bộ pipeline cho Screen Q&A mà không chạm vào luồng comic.
- **Tests written FIRST (TDD)**: `tests/test_screen_pipeline_cli.py`.
- **Tiêu chí nghiệm thu**: CLI khởi chạy độc lập cho Screen Q&A.

### Task 3.2: Screen Research & Screen Script Writer Mới (Fix 4)
- **Phân loại**: ADDITIVE.
- **Worker**: `video-qa/p3-core`.
- **Files to touch**: `stages/stage_1/screen_research.py` (mới), `stages/stage_3/screen_script.py` (mới).
- **Nội dung thực hiện**:
  - **KHÔNG SỬA `write_script.py`**: Tạo `stages/stage_3/screen_script.py` chuyên viết kịch bản narration cite `Title (Year)`.
  - Tạo `stages/stage_1/screen_research.py`: Nghiên cứu từ Screen Fandom wikis, sinh `screen_context.json`, hoàn toàn không gọi luồng Batcave của `answer_research.py:632`.
- **Tests written FIRST (TDD)**: `tests/test_screen_research_and_script.py`.
- **Tiêu chí nghiệm thu**: Sinh kịch bản chuẩn phim/năm không phụ thuộc vào comic.

### Task 3.3: Screen Shot Builder Mới (Fix 4)
- **Phân loại**: ADDITIVE.
- **Worker**: `video-qa/p3-visual`.
- **Files to touch**: `stages/stage_5/screen_shots.py` (mới).
- **Nội dung thực hiện**:
  - **KHÔNG SỬA `shots.py`**: Tạo `stages/stage_5/screen_shots.py` với hàm `build_shots_for_screen_qa`.
  - Phân chia shot theo caption chunks, hoàn toàn không phụ thuộc vào danh sách panel truyện (`_panel_pool`).
- **Tests written FIRST (TDD)**: `tests/test_screen_shot_builder.py`.
- **Tiêu chí nghiệm thu**: Sinh danh sách Shot hợp lệ khi số lượng panel bằng 0.

### Task 3.4: Chuỗi Fallback 4 Cấp Độ Không Bao Giờ Crash (Fix 11)
- **Phân loại**: ADDITIVE (Trong module mới).
- **Worker**: `video-qa/p3-visual`.
- **Files to touch**: `stages/stage_5/screen_shots.py`, `utils/text_card.py`.
- **Nội dung thực hiện**:
  - **KHÔNG SỬA `render_shot` TRONG `shots.py`**: Tạo hàm render riêng `render_screen_shot` trong `screen_shots.py`.
  - Triển khai chuỗi fallback 4 cấp: Clip chính $\rightarrow$ Clip dự phòng $\rightarrow$ Ảnh tĩnh HD Ken Burns $\rightarrow$ Thẻ chữ đồ họa (`utils/text_card.py`).
  - Tuyệt đối không để xảy ra `RuntimeError` do thiếu `source_image`.
- **Tests written FIRST (TDD)**: `tests/test_screen_qa_fallbacks.py`.
- **Tiêu chí nghiệm thu**: Giả lập hỏng file/mạng, pipeline luôn render ra video hoàn chỉnh, không bao giờ crash.

### Task 3.5: Giao Diện Screen Review UI
- **Phân loại**: ADDITIVE.
- **Worker**: `video-qa/p3-visual`.
- **Files to touch**: `ui/screens/s_screen_review.py` (mới).
- **Nội dung thực hiện**: Màn hình review dành riêng cho Screen Q&A (ẩn gallery comic, chỉ hiện danh sách clip và preview 9:16).
- **Tiêu chí nghiệm thu**: Giao diện trực quan, dễ duyệt cho Screen Q&A.

### Task 3.6: Kiểm Thử TDD End-to-End Screen Q&A
- **Phân loại**: ADDITIVE.
- **Worker**: `video-qa/p3-core` & `video-qa/p3-visual`.
- **Files to touch**: `tests/test_screen_qa_end_to_end.py` (mới).
- **Nội dung thực hiện**: Test trọn vẹn luồng Screen Q&A từ input câu hỏi đến video MP4.
- **Tiêu chí nghiệm thu**: 100% tests pass.

---

## 6. Giai Đoạn Hợp Nhất & Bàn Giao Option A (Final Delivery Phase)

### Task 3.7: Hợp Nhất Chi Nhánh & Kiểm Thử Tích Hợp Trên Windows Server (Fix 12 & 13)
- **Worker**: Integrator (`comic-book-pipeline-12`).
- **Nội dung thực hiện**:
  - Lắng nghe báo cáo hoàn thành từ các worker `video-qa/p1-verify`, `video-qa/p3-core`, `video-qa/p3-visual`.
  - Merge các local branch vào `feat/video-qa-hybrid`, giải quyết triệt để conflict.
  - Chạy full test suite trên Windows Server trong thư mục riêng `D:\code\cbp-video-test-int` trên cổng `8562`.
  - Push nhánh `feat/video-qa-hybrid`.
  - **BÁO CÁO VÀ CHỜ LỆNH PHÊ DUYỆT CỦA MASTER (Gated Checkpoint)**.

### Task 3.8: Triển Khai Production & Demo Master (Master Decision b / Option A Final Step)
- **Worker**: Integrator (`comic-book-pipeline-12`).
- **Điều kiện tiên quyết**: **CHỈ THỰC HIỆN KHI MASTER ĐÃ ĐỒNG Ý RÕ RÀNG (EXPLICIT OK)**.
- **Nội dung thực hiện**:
  - Pull nhánh `feat/video-qa-hybrid` vào thư mục production `D:\code\comic-book-pipeline`.
  - Khởi động lại UI trên cổng `8550` với cờ `ENABLE_VIDEO_CLIPS=1`.
  - Demo trực tiếp cho Master qua mạng LAN.
- **Tiêu chí nghiệm thu**: Trình diễn thành công tính năng cho Master trên môi trường production của server.

---

## 7. Bảng Tổng Hợp Phân Bổ Task & Ma Trận Trách Nhiệm

| Mã Task | Tên Nhiệm Vụ | Phân Loại | Worker Phụ Trách | File Chạm Vào |
|---|---|---|---|---|
| **0.1** | Setup nhánh tích hợp & server clone | ADDITIVE | `comic-book-pipeline-12` | Git repo |
| **0.2** | Sửa whip transition bằng Mirroring (Fix 6) | TOUCHES STABLE | `comic-book-pipeline-12` | `stage_5/pipeline.py:706` |
| **0.3** | Thu thập P0 Baseline Byte-Identity (Fix 7) | ADDITIVE | `comic-book-pipeline-12` | `tests/fixtures/p0_baseline.json` |
| **1.1** | Feature flags, Atempo & Seed conditional (Fix 1, 2) | ADDITIVE | `comic-book-pipeline-12` | `config.py` |
| **1.2** | FastAPI launcher chỉ bọc khi flag ON (Fix 3) | TOUCHES STABLE | `comic-book-pipeline-12` | `ui/__main__.py:87` |
| **1.3** | Background TTS, worker seed & chunk cache (Fix 8) | TOUCHES STABLE | `comic-book-pipeline-12` | `stage_4/_chatterbox_worker.py`, `pipeline.py` |
| **1.4** | Beat window math & Windows keep-awake | ADDITIVE | `comic-book-pipeline-12` | `stage_4/beat_timing.py` |
| **1.5** | UI Review Gate: Reset toàn bộ project khi sửa script (Fix 5) | TOUCHES STABLE | `video-qa/p1-verify` | `ui/screens/s_review_gate.py` |
| **1.6** | Web App Tab `/moments_review` (8 threads) | ADDITIVE | `comic-book-pipeline-12` | `ui/web_routes.py`, `clip_fetch.py` |
| **1.7** | Tải section yt-dlp & Preview 9:16 (Fix 9) | ADDITIVE | `comic-book-pipeline-12` | `clip_fetch.py`, `stage_5/clips.py` |
| **1.8** | Fit Math & Hard Cut tại Render Stage 5 (Fix 10) | TOUCHES STABLE | `comic-book-pipeline-12` | `stage_5/clips.py`, `pipeline.py` |
| **1.9** | Test TDD hồi quy byte-identical P1 | ADDITIVE | `video-qa/p1-verify` | `tests/test_video_qa_hybrid.py` |
| **1.10** | Test Windows Server & Checkpoint P1 | ADDITIVE | `video-qa/p1-verify` | Server `D:\code\cbp-video-test` |
| **2.1** | Pydantic Schema cho Media Router | ADDITIVE | `comic-book-pipeline-12` | `research_scout/router_schema.py` |
| **2.2** | Gemini 2.5 Flash Lite LLM Client | ADDITIVE | `comic-book-pipeline-12` | `research_scout/media_router.py` |
| **2.3** | Batcave Issue Deep Verifier 4 bước | ADDITIVE | `comic-book-pipeline-12` | `stage_1/batcave_verifier.py` |
| **2.4** | Deterministic Rules & Safety Net | ADDITIVE | `comic-book-pipeline-12` | `research_scout/router_rules.py` |
| **2.5** | Test Windows Server & Checkpoint P2 | ADDITIVE | `comic-book-pipeline-12` | Server `D:\code\cbp-video-test` |
| **3.1** | Đăng ký mode mới & `screen_pipeline.py` CLI (Fix 4) | ADDITIVE | `video-qa/p3-core` | `stages/screen_pipeline.py` |
| **3.2** | Screen Research & `screen_script.py` Writer (Fix 4) | ADDITIVE | `video-qa/p3-core` | `stage_1/screen_research.py`, `stage_3/screen_script.py` |
| **3.3** | Screen Shot Builder mới `screen_shots.py` (Fix 4) | ADDITIVE | `video-qa/p3-visual` | `stages/stage_5/screen_shots.py` |
| **3.4** | Chuỗi Fallback 4 cấp độ không crash (Fix 11) | ADDITIVE | `video-qa/p3-visual` | `stages/stage_5/screen_shots.py` |
| **3.5** | Screen Review UI mới | ADDITIVE | `video-qa/p3-visual` | `ui/screens/s_screen_review.py` |
| **3.6** | Test TDD end-to-end Screen Q&A | ADDITIVE | `video-qa/p3-core` | `tests/test_screen_qa_end_to_end.py` |
| **3.7** | Hợp nhất chi nhánh & E2E Test Server (Fix 12, 13) | ADDITIVE | `comic-book-pipeline-12` | Server `D:\code\cbp-video-test-int` |
| **3.8** | Production Deploy & Demo Master (Option A Final) | TOUCHES PROD | `comic-book-pipeline-12` | Server `D:\code\comic-book-pipeline` |
