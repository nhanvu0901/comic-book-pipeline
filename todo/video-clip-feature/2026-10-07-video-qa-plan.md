# IMPLEMENTATION PLAN: Tích Hợp Video Clips Vào Pipeline Q&A

| Kế hoạch | Trạng thái | Ngày lập | Phiên bản |
|---|---|---|---|
| `todo/video-clip-feature/2026-10-07-video-qa-plan.md` | Bản kế hoạch chi tiết (Chờ Master duyệt) | 2026-10-07 | 1.0 |

---

## 1. Quy Trình Phân Phối & Nguyên Tắc An Toàn (Delivery Guidelines)

1. **Chỉ thực thi khi Master đồng ý**: Đây là tài liệu kế hoạch. Không chỉnh sửa bất kỳ dòng code nào cho đến khi Master phê duyệt.
2. **Quy trình triển khai (Gated Delivery)**:
   $$\text{Viết Code} \longrightarrow \text{Test trên Windows Checkout phụ} \longrightarrow \text{Commit \& Push} \longrightarrow \text{Demo Master trên Server}$$
3. **Môi trường Server độc lập**:
   - Mọi thao tác kiểm thử trên Windows Server được thực hiện qua SSH tunnel vào thư mục clone phụ: `D:\code\cbp-video-test`.
   - Tuyệt đối không can thiệp thư mục production `D:\code\comic-book-pipeline` và không thay đổi cấu hình firewall.
4. **Bảo toàn nhánh ổn định (Zero-Regression Guard)**:
   - Mọi task tác động vào đường chạy ổn định (stable path) đều được bảo vệ bằng guard/flag. Khi flag `ENABLE_VIDEO_CLIPS=0` hoặc project không có manifest clip, đầu ra của pipeline phải **byte-identical (SHA256 trùng khớp 100%)**.

---

## 2. Giai Đoạn P0: Chuẩn Bị & Sửa Lỗi Whip-Bridge Transition

Mục tiêu: Thiết lập nhánh tích hợp và sửa dứt điểm lỗi vệt sọc đứng tại điểm nối giữa video clip và panel.

### Task 0.1: Thiết Lập Nhánh Tích Hợp & Môi Trường Thử Nghiệm
- **Phân loại**: ADDITIVE (Không ảnh hưởng code).
- **Files to touch**: Git repository branches.
- **Nội dung thực hiện**: Tạo nhánh tích hợp `feat/video-qa-hybrid` từ điểm mốc `ao/comic-book-pipeline-3/root`. Thiết lập checkout phụ tại `D:\code\cbp-video-test` trên Windows Server.
- **Tests written FIRST**: N/A (Thao tác quản lý mã nguồn).
- **Tiêu chí nghiệm thu (Acceptance Criteria)**:
  - Nhánh mới tạo sạch sẽ, đầy đủ 50 tests hiện tại của `tests/test_video_clips.py` pass 100%.
  - Windows Server clone thành công nhánh phụ vào `D:\code\cbp-video-test`, venv hoạt động tốt.

### Task 0.2: Sửa Lỗi Vệt Sọc Đứng Trong Whip-Bridge Transition
- **Phân loại**: TOUCHES STABLE PATH (Áp dụng cho mọi hiệu ứng whip transition).
- **Guard / Cơ chế bảo vệ**: Hàm toán học xử lý padding biên; giữ nguyên tổng thời lượng bridge và mốc thời gian mượn của cảnh kề (`pipeline.py:631-649`).
- **Files to touch**: `stages/stage_5/pipeline.py` (Dòng 706–720).
- **Nội dung thực hiện**:
  - Tại `_shift_up`: Bỏ cơ chế crop 1px row đáy `img.crop((0, OUTPUT_H - 1, OUTPUT_W, OUTPUT_H)).resize(...)`. Thay bằng trích xuất dải biên đa hàng (`min(px, 32)` px) áp dụng bilinear interpolation để triệt tiêu sọc đơn sắc kéo dài.
  - Tại `_shift_from_below`: Sửa tương tự cho dải biên đỉnh đối xứng.
- **Tests written FIRST (TDD)**:
  - Tạo `tests/test_whip_bridge.py`:
    - `test_shift_up_no_vertical_streaks()`: Sinh ảnh test có độ dốc màu dọc, gọi `_shift_up(img, px=40)`. Kiểm tra độ lệch chuẩn của gradient vùng đáy $> 0$ (không bị 1 hàng pixel lặp lại giống hệt nhau).
    - `test_shift_from_below_no_vertical_streaks()`: Kiểm tra tương tự cho vùng đỉnh.
    - `test_whip_bridge_duration_and_audio_sync_preserved()`: Đảm bảo tổng thời lượng không đổi.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**:
  - 100% tests mới trong `test_whip_bridge.py` pass.
  - Video render thực tế tại đoạn nối clip $\leftrightarrow$ panel không còn hiện tượng kéo sọc đứng.

### Task 0.3: Kiểm Thử & Checkpoint P0 Trên Windows Server
- **Nội dung thực hiện**: Chạy test suite trên `D:\code\cbp-video-test` qua SSH tunnel.
- **Lệnh thực thi**: `pytest tests/test_whip_bridge.py tests/test_video_clips.py`.
- **Checkpoint P0**: Báo cáo Master kết quả test và clip mẫu trước khi bước vào P1.

---

## 3. Giai Đoạn P1: Lớp Video Clip Hybrid Trong Comic Q&A

Mục tiêu: Cho phép chọn và phát video clip trên từng beat trong Comic Q&A, giữ comic làm nguồn gốc, quản lý qua cờ `ENABLE_VIDEO_CLIPS=0`.

### Task 1.1: Định Nghĩa Feature Flag & Biến Cấu Hình
- **Phân loại**: ADDITIVE.
- **Files to touch**: `config.py`, `.env.example`.
- **Nội dung thực hiện**:
  - Khai báo cờ `ENABLE_VIDEO_CLIPS = bool(int(os.getenv("ENABLE_VIDEO_CLIPS", "0")))`.
  - Khai báo các thông số fit clip: `CLIP_SPEED_MIN = float(os.getenv("CLIP_SPEED_MIN", "0.8"))`, `CLIP_SPEED_MAX = float(os.getenv("CLIP_SPEED_MAX", "1.25"))`, `CLIP_MAX_HOLD = float(os.getenv("CLIP_MAX_HOLD", "0.3"))`.
  - Khai báo `POST_ATEMPO = float(os.getenv("POST_ATEMPO", "1.15"))` và `CHATTERBOX_SEED = int(os.getenv("CHATTERBOX_SEED", "42"))`.
- **Tests written FIRST (TDD)**:
  - `tests/test_qa_clip_config.py`:
    - `test_default_flags_off()`: Đảm bảo `ENABLE_VIDEO_CLIPS` mặc định là `False`.
    - `test_custom_env_flags()`: Kiểm tra nạp đúng biến khi gán env.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Khi không đặt env, toàn bộ flag giữ nguyên giá trị mặc định an toàn.

### Task 1.2: Kiến Trúc Fast-API ASGI Server & LAN Router
- **Phân loại**: TOUCHES STABLE PATH (`ui/__main__.py:87` cho chế độ `--lan`).
- **Guard / Cơ chế bảo vệ**: Chế độ desktop `ft.run(main)` tại `ui/__main__.py:67` hoàn toàn không đổi. Khi chạy `--lan`, các route clip chỉ được nạp; nếu flag `ENABLE_VIDEO_CLIPS=0`, route trả về thông báo tính năng tắt.
- **Files to touch**: `ui/__main__.py`, `ui/web_routes.py` (mới).
- **Nội dung thực hiện**:
  - Tạo `ui/web_routes.py`: Định nghĩa `APIRouter` chứa các endpoint `/moments_review`, `/api/pick_moment`, `/api/beat_tts_status`.
  - Sửa `ui/__main__.py:87`: Khi `--lan`, xuất `flet_asgi = ft.run(main, export_asgi_app=True, assets_dir=str(PROJECTS_ROOT))`, khởi tạo FastAPI app, nạp `clip_router` **TRƯỚC**, sau đó mới `app.mount("/", flet_asgi)`. Chạy uvicorn trên cổng `8550`.
- **Tests written FIRST (TDD)**:
  - `tests/test_fastapi_launcher.py`:
    - `test_routes_precede_flet_mount()`: Giả lập HTTP client gọi `/moments_review`, đảm bảo trả về HTTP 200 HTML chứ không bị Flet catch-all nuốt.
    - `test_desktop_mode_untouched()`: Đảm bảo lệnh gọi desktop không khởi động uvicorn.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Mở trình duyệt truy cập `http://localhost:8550/moments_review` trả về HTML đúng; truy cập `http://localhost:8550/` mở ứng dụng Flet bình thường.

### Task 1.3: Deterministic Background TTS Runner & Caching Hạt Nhân
- **Phân loại**: TOUCHES STABLE PATH (`stages/stage_4/chatterbox_tts.py:173`, `stages/stage_4/pipeline.py:151`).
- **Guard / Cơ chế bảo vệ**: Nếu không có `CHATTERBOX_SEED`, giữ nguyên temperature ngẫu nhiên. Cache WAV đọc nếu có, không có thì tổng hợp bình thường.
- **Files to touch**: `stages/stage_4/chatterbox_tts.py`, `stages/stage_4/pipeline.py`.
- **Nội dung thực hiện**:
  - Tại `chatterbox_tts.py`: Bổ sung tham số `seed: int | None = 42` vào payload gửi sang subprocess worker `_WORKER`. Worker gán seed cố định cho torch/python RNG.
  - Bổ sung cơ chế cache WAV theo hash:
    `key = sha256(f"{text}|{voice}|{exaggeration}|{cfg}|{seed}|{atempo}".encode()).hexdigest()`
    Lưu file tại `projects/<p>/cache/tts/<key>.wav`.
  - Tại `stage_4/pipeline.py:151`: Kiểm tra cache WAV trước khi gọi tổng hợp, tái sử dụng các file đã sinh nền từ Review Gate.
- **Tests written FIRST (TDD)**:
  - `tests/test_chatterbox_deterministic.py`:
    - `test_chatterbox_fixed_seed_bit_identical()`: Tổng hợp cùng 1 câu 2 lần với cùng seed trên cùng máy, assert hash file WAV giống nhau 100%.
    - `test_wav_cache_reuse_in_stage_4()`: Tạo cache WAV giả lập, chạy Stage 4 pipeline và xác minh không gọi synthesize mới.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Chạy 2 lần background TTS trên cùng câu cho ra file WAV bit-identical. Stage 4 nhận diện và tái sử dụng toàn bộ cache.

### Task 1.4: Bộ Tính Toán Beat Window & Quản Lý Keep-Awake Windows
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/stage_4/beat_timing.py` (mới).
- **Nội dung thực hiện**:
  - Xây dựng hàm `calculate_beat_durations(narration, wav_timings)`:
    - Phân bổ từ câu sang beat theo tỷ lệ từ ngữ (`_even_words`).
    - Áp dụng gap absorption (`shots.py:797-803`).
    - Gộp các beat $< 1.5\text{s}$ (`QA_MIN_SHOT_SECONDS`).
    - Khấu trừ $0.12\text{s}$ mỗi bên nếu có whip transition.
  - Xây dựng hàm quản lý trạng thái máy tính:
    `set_keep_awake(True/False)` sử dụng `ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)` khi chạy trên Windows.
- **Tests written FIRST (TDD)**:
  - `tests/test_beat_timing.py`:
    - `test_beat_window_calculation_math()`: So sánh kết quả tính beat window với fixture chuẩn của Stage 5.
    - `test_whip_deduction_and_min_duration()`: Đảm bảo không có beat nào $< 0.4\text{s}$ sau khi khấu trừ.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Kết quả tính toán thời lượng beat khớp chính xác với thời lượng shot tương ứng khi render ở Stage 5.

### Task 1.5: UI Review Gate - Nút "Dùng MP4", Invalidation & PubSub Sync
- **Phân loại**: TOUCHES STABLE PATH (`ui/screens/s_review_gate.py:1474-1510`).
- **Guard / Cơ chế bảo vệ**: Bọc trong điều kiện `if config.ENABLE_VIDEO_CLIPS:`. Khi cờ OFF, giao diện không render icon "Dùng MP4" và không kích hoạt background TTS.
- **Files to touch**: `ui/screens/s_review_gate.py`.
- **Nội dung thực hiện**:
  - Khi mở Review Gate với `ENABLE_VIDEO_CLIPS=1`: Khởi chạy background task tổng hợp TTS cho toàn bộ câu trong kịch bản.
  - Trên mỗi Beat Card: Thêm icon `ft.IconButton(ft.Icons.VIDEO_FILE_OUTLINED, tooltip="Dùng MP4")`. Khi click, gọi `page.launch_url(f"/moments_review?project={project}&beat={beat_key}")` mở tab mới. Đẩy câu của beat này lên đầu priority queue TTS.
  - Đăng ký `page.pubsub`: Khi nhận event `clip_picked` từ tab web, cập nhật trạng thái Beat Card sang nhãn MP4 màu xanh.
  - Khi người dùng chỉnh sửa chữ narration (`_on_text` / `_on_frag_text`): Xóa sạch panel lock và MP4 selection của các beat thuộc câu sửa; hủy cache WAV của câu đó và đẩy vào hàng đợi tổng hợp lại.
- **Tests written FIRST (TDD)**:
  - `tests/test_review_gate_clip_ui.py`:
    - `test_clip_button_hidden_when_flag_off()`: Assert nút MP4 không có trong cây control khi cờ OFF.
    - `test_edit_narration_clears_locks_and_clips()`: Mô phỏng sự kiện sửa text, kiểm tra lock và clip bị xóa.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Giao diện cập nhật tức thì khi chọn clip ở tab ngoài; sửa kịch bản vô hiệu hóa đúng các lựa chọn liên quan.

### Task 1.6: Web App Tab `/moments_review` (YouTube IFrame API & Parallel Search)
- **Phân loại**: ADDITIVE.
- **Files to touch**: `ui/web_routes.py`, `stages/clip_fetch.py`.
- **Nội dung thực hiện**:
  - Xây dựng giao diện HTML tại route `/moments_review`:
    - Hiển thị danh sách kết quả từ `clip_fetch.moment_search`.
    - Nhúng iframe YouTube Player API (`enablejsapi=1`).
    - Nút mốc thời gian gợi ý (chapters, subtitles, peaks).
    - Link "Xem trên YouTube tại t" cho từng video.
    - Nút "Dùng từ đây" gọi `player.getCurrentTime()` và POST JSON `{project, beat, video_id, start}` về `/api/pick_moment`.
  - Tối ưu hóa `moment_search`: Song song hóa 8 luồng qua `ThreadPoolExecutor` để lấy metadata; cache kết quả theo `(beat, query)`.
- **Tests written FIRST (TDD)**:
  - `tests/test_moments_web_review.py`:
    - `test_parallel_moment_search_performance()`: Kiểm tra thời gian tìm kiếm với mock 8 video hoàn thành dưới 15s.
    - `test_pick_moment_api_pubsub_broadcast()`: Gửi POST `/api/pick_moment`, kiểm tra pubsub nhận đúng payload.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Tab web tải nhanh, nhúng player mượt mà, bấm chọn gửi dữ liệu chính xác về Flet.

### Task 1.7: Tải Section Với yt-dlp & Tạo Preview 9:16
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/clip_fetch.py`, `stages/stage_5/clips.py`.
- **Nội dung thực hiện**:
  - Viết hàm `fetch_clip_section(url, out_dir, start, duration)`:
    - Chạy: `yt-dlp --download-sections "*{start}-{start+duration+2.0}" --force-keyframes-at-cuts ...`
    - Đưa file về chuẩn h264 yuv420p 30fps.
  - Viết hàm `render_clip_preview(clip_path, start, beat_duration)`:
    - Render nhanh clip 9:16 (contain+blur) đúng độ dài beat để Master xem thử trước khi chốt.
- **Tests written FIRST (TDD)**:
  - `tests/test_download_section.py`:
    - `test_ytdlp_download_section_command_args()`: Kiểm tra chuỗi đối số `-download-sections` và keyframe cut.
    - `test_preview_aspect_ratio_and_duration()`: Xác minh file preview sinh ra đúng 1080x1920 và đúng số frame.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Dung lượng tải về giảm $> 14\times$ so với tải toàn bộ video; preview hiển thị chuẩn xác 9:16.

### Task 1.8: Triển Khai Fit Math & Hard Cut Tại Render Stage 5
- **Phân loại**: TOUCHES STABLE PATH (`stages/stage_5/clips.py:563-600`, `stages/stage_5/shots.py:2995`).
- **Guard / Cơ chế bảo vệ**: Chỉ kích hoạt khi shot có `clip_path`. Nếu không có, toàn bộ logic render panel Ken Burns và xfade dissolve giữ nguyên 100%.
- **Files to touch**: `stages/stage_5/clips.py`, `stages/stage_5/pipeline.py`.
- **Nội dung thực hiện**:
  - Triển khai thuật toán Fit Math trong `render_clip_shot`:
    1. Đo thời lượng thực tế của shot $T$.
    2. Cắt từ $S$. Nếu thiếu/thừa, áp dụng speed filter trong khoảng $[0.8, 1.25]$.
    3. Nếu vẫn thiếu sau speed: giữ frame cuối $\le 0.3\text{s}$ bằng `tpad`.
  - Tại `stages/stage_5/pipeline.py`: Xử lý transition:
    - Shot clip: Ép **HARD CUT** tại biên nối trước và sau shot.
    - Shot panel: Giữ nguyên hòa tan **DISSOLVE** (`XFADE_DURATION=0.25s`).
- **Tests written FIRST (TDD)**:
  - `tests/test_clip_fit_math.py`:
    - `test_clip_speed_within_bounds()`: Kiểm tra hệ số speed không vượt quá 0.8 và 1.25.
    - `test_clip_hard_cut_transition_boundaries()`: Xác minh concat graph tạo cut cứng quanh clip shot.
    - `test_frozen_tail_capped_at_max_hold()`: Kiểm tra hold frame không vượt quá 0.3s.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Video render mượt mà, âm thanh không bị lệch pha một frame nào; shot clip hiển thị đúng nhịp hành động.

### Task 1.9: Kiểm Thử TDD Hồi Quy Toàn Diện Cho Giai Đoạn P1
- **Phân loại**: ADDITIVE (Bộ test).
- **Files to touch**: `tests/test_video_qa_hybrid.py` (mới).
- **Nội dung thực hiện**:
  - Viết test kiểm tra tính toàn vẹn:
    - `test_byte_identical_when_flag_off()`: Chạy fixture pipeline Q&A chuẩn với `ENABLE_VIDEO_CLIPS=0`. Đối chiếu SHA256 của `shots.json` và `video_silent.mp4` với fixture gốc. Phải trùng khớp 100%.
    - `test_clip_shot_contract_compliance()`: Kiểm tra mọi clip shot sinh ra thỏa mãn contract h264 yuv420p 1080x1920 30fps no audio.
    - `test_rollback_to_panel_preserves_original_shot()`: Kiểm tra thao tác hoàn tác về panel khôi phục đúng panel lock ban đầu.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Toàn bộ suite test pass hoàn toàn trên máy dev.

### Task 1.10: Triển Khai Kiểm Thử Trên Windows Server & Checkpoint P1
- **Nội dung thực hiện**:
  - Đồng bộ code lên `D:\code\cbp-video-test` trên Windows Server qua SSH.
  - Chạy full test: `pytest tests/test_video_qa_hybrid.py tests/test_video_clips.py`.
  - Chạy thử nghiệm 1 project Q&A thực tế, mở web UI qua LAN port 8550, chọn 2 clip MP4 cho 2 beat.
  - Render video hoàn chỉnh, kiểm tra độ mượt trên VLC.
- **Checkpoint P1**: Báo cáo Master video mẫu hybrid và xin phê duyệt trước khi chuyển sang P2.

---

## 4. Giai Đoạn P2: Type-Safe Media Source Router

Mục tiêu: Tự động phân loại câu hỏi Q&A thuộc Comic hay Screen Media một cách an toàn kiểu dữ liệu và có tính xác định cao.

### Task 2.1: Xây Dựng Schema Pydantic Cho Router
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/research_scout/router_schema.py` (mới).
- **Nội dung thực hiện**:
  - Định nghĩa `PrimaryMedium` (`comic`, `film`, `tv_animation`, `game`, `mixed`).
  - Định nghĩa `VisualSource` (`comic`, `youtube`, `both`).
  - Định nghĩa model `RoutedItem` và `QuestionRouteResponse` theo đúng schema đã thử nghiệm thành công trong Spike.
- **Tests written FIRST (TDD)**:
  - `tests/test_router_schema.py`:
    - `test_schema_validation_valid_json()`: Xác thực payload hợp lệ.
    - `test_schema_rejects_invalid_enums()`: Đảm bảo throw `ValidationError` khi medium nằm ngoài enum.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Schema chuẩn hóa 100%, serialize/deserialize không lỗi.

### Task 2.2: LLM Router Client (Gemini 2.5 Flash Lite)
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/research_scout/media_router.py` (mới).
- **Nội dung thực hiện**:
  - Tích hợp OpenRouter/Google API gọi `google/gemini-2.5-flash-lite`.
  - Đặt `temperature = 0.0`, ép `response_format = {"type": "json_object"}` kèm schema prompt.
  - Bổ sung cơ chế retry tối đa 2 lần nếu gặp lỗi validation Pydantic.
- **Tests written FIRST (TDD)**:
  - `tests/test_media_router_client.py`:
    - `test_router_llm_call_with_mock_response()`: Kiểm tra xử lý phản hồi và tính toán token/chi phí.
    - `test_router_handles_malformed_json_retry()`: Mô phỏng JSON lỗi lần đầu, retry thành công lần 2.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Tỷ lệ lỗi schema 0%, độ trễ trung bình $\le 2.5\text{s}$, chi phí $\le \$0.0004/\text{câu}$.

### Task 2.3: Kiểm Tra Batcave Sâu Cấp Issue (Batcave Issue Verifier)
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/stage_1/batcave_verifier.py` (mới).
- **Nội dung thực hiện**:
  - Viết hàm `verify_batcave_issue(series_name, issue_number, year=None) -> bool`:
    1. Tìm kiếm series theo tên và năm xuất bản (`disambiguation by year`).
    2. Gọi `discover_issues` bóc tách `window.__DATA__.chapters`.
    3. So khớp chính xác số issue.
    4. Gửi ping `getChapterData` xác nhận trang truyện tồn tại.
- **Tests written FIRST (TDD)**:
  - `tests/test_batcave_issue_verifier.py`:
    - `test_batcave_verifier_true_positive()`: Thử nghiệm với ASM 1963 #121 $\rightarrow$ trả về `True`.
    - `test_batcave_verifier_false_positive_prevention()`: Thử nghiệm với ASM 2018 #121 (không tồn tại) $\rightarrow$ trả về `False`.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Loại bỏ hoàn toàn lỗi dương tính giả của các series trùng tên khác năm.

### Task 2.4: Bộ Quy Tắc Quyết Định Deterministic & Tích Hợp Router
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/research_scout/router_rules.py` (mới).
- **Nội dung thực hiện**:
  - Triển khai hàm `resolve_media_route(routed_items, question_text) -> str`:
    - Kiểm tra: Không có comic/mixed AND kiểm tra Batcave issue thất bại AND tìm thấy video clips $\implies$ `screen_qa`.
    - Ngược lại $\implies$ `comic_qa`.
- **Tests written FIRST (TDD)**:
  - `tests/test_router_rules.py`:
    - `test_civil_war_route_safety_net()`: Câu hỏi "Why did Captain America and Iron Man fight?" $\rightarrow$ dù LLM nghiêng về phim nhưng Batcave có truyện Civil War #1 $\rightarrow$ bắt buộc trả về `comic_qa`.
    - `test_endgame_routes_to_screen()`: Câu hỏi Endgame $\rightarrow$ Batcave không có $\rightarrow$ trả về `screen_qa`.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Đạt độ chính xác 10/10 trên bộ testset `part1_testset.json`.

### Task 2.5: Kiểm Thử Trên Windows Server & Checkpoint P2
- **Nội dung thực hiện**: Chạy toàn bộ test router trên Windows checkout phụ. Xác thực kết nối mạng đến You.com và Batcave.
- **Checkpoint P2**: Trình bày kết quả định tuyến và xin ý kiến Master trước khi triển khai P3.

---

## 5. Giai Đoạn P3: Chế Độ Độc Lập `screen_qa`

Mục tiêu: Xây dựng chế độ pipeline mới `screen_qa`, tách biệt hoàn toàn khỏi luồng tải truyện tranh, hình ảnh 100% là clips/stills/text cards, không bao giờ crash.

### Task 3.1: Đăng Ký Chế Độ Pipeline `screen_qa`
- **Phân loại**: ADDITIVE.
- **Files to touch**: `config.py`.
- **Nội dung thực hiện**:
  - Bổ sung `SCREEN_QA = "screen_qa"` vào enum / danh sách mode được hỗ trợ.
  - Thêm cấu hình riêng cho Screen QA (bỏ qua download comic, bật clip builder).
- **Tests written FIRST (TDD)**:
  - `tests/test_screen_qa_mode_registration.py`:
    - `test_screen_qa_mode_valid()`: Đảm bảo CLI chấp nhận `--mode screen_qa`.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Pipeline nhận diện mode mới mà không làm xáo trộn các mode cũ (`recap`, `explore_answer`, `micro_moment`).

### Task 3.2: Module Nghiên Cứu Screen Canon & Định Dạng Narration
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/stage_1/screen_research.py` (mới), `stages/stage_3/write_script.py`.
- **Nội dung thực hiện**:
  - Xây dựng `screen_research.py`: Bóc tách thông tin từ Screen Fandom wikis, sinh file `screen_context.json` (thay thế cho `answer_context.json` và `comic_context.json`). Tuyệt đối không gọi tới `stages/stage_1/answer_research.py:632`.
  - Trong `write_script.py`: Thêm nhánh xử lý cho `screen_qa`: Trích dẫn nguồn theo cú pháp `Tác phẩm (Năm phát hành)` (ví dụ: *Avengers: Infinity War (2018)*) thay vì số issue truyện tranh.
- **Tests written FIRST (TDD)**:
  - `tests/test_screen_research_and_script.py`:
    - `test_screen_research_creates_context_without_comic_urls()`: Đảm bảo tạo context hợp lệ không chứa reader_url.
    - `test_screen_narration_cites_movie_and_year()`: Kiểm tra prompt và đầu ra narration trích dẫn đúng chuẩn phim/năm.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Stage 1 và Stage 3 chạy trơn tru cho Screen Q&A không phụ thuộc vào Batcave.

### Task 3.3: Shot Builder Độc Lập Cho Screen Q&A
- **Phân loại**: ADDITIVE.
- **Files to touch**: `stages/stage_5/screen_shots.py` (mới).
- **Nội dung thực hiện**:
  - Viết hàm `build_shots_for_screen_qa(narration, scene_timings, clips_manifest)`:
    - Phân chia shot dựa trên caption chunks và độ dài beat.
    - Hoàn toàn không phụ thuộc vào danh sách panel truyện tranh (`pages_by_number` hay `_panel_pool`).
    - Mỗi shot được gán clip từ manifest hoặc chuyển sang chuỗi fallback.
- **Tests written FIRST (TDD)**:
  - `tests/test_screen_shot_builder.py`:
    - `test_screen_shots_built_without_comic_pages()`: Chạy builder với `pages_by_number={}` mà không nảy sinh ngoại lệ.
    - `test_shot_durations_cover_entire_narration()`: Đảm bảo tổng thời lượng các shot khớp chính xác với timeline audio.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Builder sinh danh sách Shot hoàn chỉnh, hợp lệ, sẵn sàng render.

### Task 3.4: Chuỗi Fallback 4 Cấp Độ Không Bao Giờ Crash
- **Phân loại**: TOUCHES STABLE PATH (`stages/stage_5/shots.py:3003-3006`).
- **Guard / Cơ chế bảo vệ**: Chỉ áp dụng khi `shot.mode == "screen_qa"` hoặc khi shot không có `source_image`. Các shot của Comic Q&A vẫn giữ nguyên báo lỗi panel thiếu để tránh mất hình comic ngoài ý muốn.
- **Files to touch**: `stages/stage_5/shots.py`, `utils/text_card.py`.
- **Nội dung thực hiện**:
  - Sửa hàm `render_shot`:
    - Khi clip chính lỗi: Thử render clip dự phòng (`backup_candidate`).
    - Nếu không có clip dự phòng: Thử render ảnh tĩnh HD / custom image (`custom_images.json`) với chuyển động Ken Burns nhẹ.
    - Nếu không có ảnh tĩnh: Gọi `utils/text_card.render_text_card` tạo thẻ chữ đồ họa chứa câu trả lời tóm tắt của beat trên nền tối có logo.
    - Tuyệt đối không để xảy ra `RuntimeError` do thiếu `source_image`.
- **Tests written FIRST (TDD)**:
  - `tests/test_screen_qa_fallbacks.py`:
    - `test_fallback_to_backup_clip()`: Mô phỏng clip chính hỏng, hệ thống tự lấy clip phụ.
    - `test_fallback_to_hd_still()`: Mô phỏng cả 2 clip hỏng, lấy ảnh tĩnh.
    - `test_fallback_to_text_card_never_crashes()`: Mô phỏng không có tài nguyên nào, render ra thẻ chữ, assert return code thành công.
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Pipeline hoàn thành 100% trong mọi tình huống giả lập hỏng file hoặc đứt mạng.

### Task 3.5: Kiểm Thử TDD Toàn Diện Cho Chế Độ `screen_qa`
- **Phân loại**: ADDITIVE.
- **Files to touch**: `tests/test_screen_qa_end_to_end.py` (mới).
- **Nội dung thực hiện**:
  - Viết test end-to-end giả lập trọn vẹn 1 dự án Screen Q&A (từ khâu nhận câu hỏi, routing, script, tải clip section đến dựng video MP4 hoàn chỉnh).
- **Tiêu chí nghiệm thu (Acceptance Criteria)**: Test chạy thông suốt từ đầu đến cuối trên máy local.

### Task 3.6: Kiểm Thử Toàn Diện Trên Windows Server & Demo Master (Final Checkpoint)
- **Nội dung thực hiện**:
  - Đồng bộ toàn bộ mã nguồn lên `D:\code\cbp-video-test` trên Windows Server.
  - Chạy toàn bộ test suite dự án (`pytest`).
  - Chạy thử nghiệm thực tế 1 câu hỏi Screen Q&A (ví dụ: *"How did the Avengers travel back in time in Endgame?"*).
  - Trình duyệt qua LAN cổng 8550.
  - Kiểm tra video hoàn thiện xuất ra tại `projects/<p>/video.mp4`.
- **Final Checkpoint**: Trình diễn Master sản phẩm video thực tế trên Windows Server.

---

## 6. Tổng Hợp Danh Sách Task & Ma Trận Phân Loại

| Phase | Mã Task | Tên Nhiệm Vụ | Phân Loại | Cơ Chế Bảo Vệ (Guard) | File Chạm Vào |
|---|---|---|---|---|---|
| **P0** | **Task 0.1** | Tạo nhánh tích hợp & setup server clone | ADDITIVE | Nhánh Git riêng | Git repository |
| | **Task 0.2** | Sửa lỗi vệt sọc đứng Whip-Bridge | TOUCHES STABLE | Thuật toán padding bilinear | `stages/stage_5/pipeline.py:706` |
| | **Task 0.3** | Kiểm thử & Checkpoint P0 | ADDITIVE | Thư mục clone phụ | Server `D:\code\cbp-video-test` |
| **P1** | **Task 1.1** | Khai báo Feature Flags & timing config | ADDITIVE | Mặc định OFF (`0`) | `config.py` |
| | **Task 1.2** | FastAPI launcher & ASGI LAN mount | TOUCHES STABLE | Desktop mode giữ nguyên | `ui/__main__.py:87`, `ui/web_routes.py` |
| | **Task 1.3** | Deterministic Background TTS & Cache | TOUCHES STABLE | Fixed seed 42, cache fallback | `stage_4/chatterbox_tts.py`, `stage_4/pipeline.py` |
| | **Task 1.4** | Beat window math & Keep-awake Windows | ADDITIVE | Module mới | `stage_4/beat_timing.py` |
| | **Task 1.5** | UI Review Gate: Nút MP4 & Invalidation | TOUCHES STABLE | Cờ `ENABLE_VIDEO_CLIPS` | `ui/screens/s_review_gate.py` |
| | **Task 1.6** | Web App Tab `/moments_review` (8 threads) | ADDITIVE | Route độc lập | `ui/web_routes.py`, `clip_fetch.py` |
| | **Task 1.7** | Tải section yt-dlp & Preview 9:16 | ADDITIVE | Hàm chuyên dụng | `clip_fetch.py`, `stage_5/clips.py` |
| | **Task 1.8** | Fit Math & Hard Cut tại Render Stage 5 | TOUCHES STABLE | Chỉ chạy khi shot có clip | `stage_5/clips.py`, `stage_5/pipeline.py` |
| | **Task 1.9** | Test TDD hồi quy byte-identical P1 | ADDITIVE | Test suite | `tests/test_video_qa_hybrid.py` |
| | **Task 1.10** | Test Windows Server & Checkpoint P1 | ADDITIVE | Checkout phụ | Server `D:\code\cbp-video-test` |
| **P2** | **Task 2.1** | Pydantic Schema cho Media Router | ADDITIVE | Module mới | `research_scout/router_schema.py` |
| | **Task 2.2** | Gemini 2.5 Flash Lite LLM Client | ADDITIVE | Module mới | `research_scout/media_router.py` |
| | **Task 2.3** | Batcave Issue Deep Verifier (4 bước) | ADDITIVE | Module mới | `stage_1/batcave_verifier.py` |
| | **Task 2.4** | Deterministic Rules & Safety Net | ADDITIVE | Module mới | `research_scout/router_rules.py` |
| | **Task 2.5** | Test Windows Server & Checkpoint P2 | ADDITIVE | Checkout phụ | Server `D:\code\cbp-video-test` |
| **P3** | **Task 3.1** | Đăng ký mode mới `screen_qa` | ADDITIVE | Flag mode mới | `config.py` |
| | **Task 3.2** | Screen Research & Script Movie Cites | ADDITIVE | Module mới | `stage_1/screen_research.py`, `stage_3/write_script.py` |
| | **Task 3.3** | Shot Builder độc lập cho Screen Q&A | ADDITIVE | Builder mới | `stage_5/screen_shots.py` |
| | **Task 3.4** | Chuỗi Fallback 4 cấp độ không crash | TOUCHES STABLE | Guard theo `mode == "screen_qa"` | `stage_5/shots.py`, `utils/text_card.py` |
| | **Task 3.5** | Test TDD end-to-end Screen Q&A | ADDITIVE | Test suite | `tests/test_screen_qa_end_to_end.py` |
| | **Task 3.6** | Test Windows & Final Demo Checkpoint | ADDITIVE | Checkout phụ | Server `D:\code\cbp-video-test` |

---

## 7. Rủi Ro Tiềm Ẩn & Đánh Giá Quyết Định Kỹ Thuật

### 7.1. Các Rủi Ro Kỹ Thuật (Open Risks)
1. **Hiện tượng YouTube Rate Limit / HTTP 429 khi lấy phụ đề**:
   - Khi tìm kiếm nhiều video liên tục, YouTube đôi khi giới hạn IP lấy phụ đề XML/VTT.
   - *Biện pháp giảm thiểu*: Hệ thống đã được thiết kế sẵn sàng: nếu mất phụ đề, thuật toán xếp hạng vẫn dựa trên Chapters và Most-replayed heatmap peaks để gợi ý mốc thời gian.
2. **Sai lệch thời lượng TTS giữa Mac và Windows**:
   - Dù cùng seed, khác biệt về nhân xử lý PyTorch (MPS trên Mac vs CPU trên Windows) có thể gây lệch tới 0.45s/beat.
   - *Biện pháp giảm thiểu*: Quy định Windows Server là nơi lưu cache WAV duy nhất làm chuẩn; máy Mac chỉ nhận kết quả đã sinh từ server.
3. **Hiện tượng ngủ (Sleep/Suspend) của Windows Server**:
   - Windows Server tự động chuyển sang chế độ ngủ sau 30-60s không tương tác bàn phím/chuột.
   - *Biện pháp giảm thiểu*: Kích hoạt liên tục `SetThreadExecutionState` trong background TTS worker để giữ luồng CPU hoạt động.

### 7.2. Đánh Giá Khách Quan Về Các Quyết Định (Decisions Critique)
1. **Về việc bắt buộc chạy Background TTS ngay khi mở Review Gate**:
   - *Nhận xét thẳng thắn*: Việc CPU Windows tốn 5–7 phút để tổng hợp 1 video Short tạo ra độ trễ chờ đợi ban đầu nếu Master muốn chọn clip ngay lập tức.
   - *Giải pháp tối ưu bổ sung*: Cơ chế **Priority Queue** (ưu tiên câu của beat được click) là quyết định cứu cánh tuyệt đối cần phải thực hiện triệt để: Master click beat nào, câu đó được ưu tiên làm trước trong ~20s thay vì phải chờ hết cả kịch bản 5–7 phút.
2. **Về việc xóa toàn bộ lựa chọn Panel khi sửa Narration**:
   - *Nhận xét thẳng thắn*: Nếu Master chỉ sửa 1 từ nhỏ không làm thay đổi nhịp, việc xóa cả lựa chọn panel đã chọn trước đó có thể gây phiền toái. Tuy nhiên, vì thời lượng âm thanh thay đổi sẽ làm vỡ timing cắt hình của clip và căn khung của panel, nên quyết định xóa là **đúng đắn về mặt kỹ thuật để triệt tiêu lỗi lệch hình-tiếng**. Để giảm tải cho Master, hệ thống chỉ xóa các beat thuộc câu bị sửa, giữ nguyên các câu khác.
