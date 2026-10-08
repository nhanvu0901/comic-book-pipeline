# SPEC: Tích Hợp Video Clips Vào Pipeline Q&A (Comic Q&A Hybrid & Screen Q&A)

| Tài liệu | Trạng thái | Ngày cập nhật | Phiên bản | Tác giả |
|---|---|---|---|---|
| `todo/video-clip-feature/2026-10-07-video-qa-spec.md` | Bản thiết kế hoàn chỉnh (Master Decisions a/b & 13 Fixes) | 2026-10-08 | 2.0 | Antigravity |

---

## 1. Mục Tiêu & Nguyên Tắc Thiết Kế

1. **Tổng quát hóa (General Mechanism)**: Tuyệt đối không hard-code tên truyện, số trang, nhân vật, số issue hay hằng số cục bộ. Mọi cơ chế (xử lý khung hình, fit timing, fallback, routing) phải áp dụng được cho mọi comic và video.
2. **Bảo toàn nhánh ổn định (Zero-Regression & Byte-Identical)**:
   - Toàn bộ tính năng video clip trong Comic Q&A nằm sau feature flag (`ENABLE_VIDEO_CLIPS=0` mặc định OFF).
   - Khi flag OFF hoặc project không có `review/clips/clips.json`: đường chạy Comic Q&A (`explore_answer`) cho ra `shots.json` và `video_silent.mp4` trùng khớp SHA256 100% với phiên bản hiện tại.
   - Chế độ thuần video `screen_qa` nằm hoàn toàn trong **CÁC MODULE MỚI (NEW MODULES ONLY)**: không can thiệp vào `write_script.py`, không can thiệp vào `render_shot` trong `shots.py`, không tái sử dụng luồng tải comic lỗi tại `answer_research.py:632`.
3. **Môi trường triển khai tách biệt & Quy trình bàn giao Option A (Delivery Option A)**:
   - Mọi thao tác thử nghiệm chỉ thực hiện trên thư mục checkout riêng trên Windows Server (ví dụ `D:\code\cbp-video-test` và `D:\code\cbp-video-test-int`) qua SSH tunnel.
   - Quy trình bàn giao:
     $$\text{Test trên checkout riêng} \longrightarrow \text{Push nhánh } \texttt{feat/video-qa-hybrid} \longrightarrow \mathbf{\text{CHỈ KHI CÓ LỆNH OK CỦA MASTER}} \longrightarrow \text{Pull vào } \texttt{D:\setminus code\setminus comic-book-pipeline} \longrightarrow \text{Restart UI 8550}$$
   - Tuyệt đối không can thiệp thư mục production `D:\code\comic-book-pipeline` và không đụng vào firewall Windows trước khi có lệnh rõ ràng của Master.

---

## 2. Kiến Trúc Tổng Thể & Feature Flags

Hệ thống bổ sung 2 luồng phục vụ video clips:
- **P1 - Comic Q&A Hybrid**: Truyện tranh vẫn là nguồn hình ảnh gốc. Master có thể chọn thay thế panel Ken Burns của từng beat bằng video clip ngắn (tắt tiếng, căn 9:16).
- **P3 - Screen Q&A**: Chế độ độc lập dành riêng cho các câu hỏi điện ảnh/hoạt hình (MCU, DCAU...). Hình ảnh 100% là video clip/hình tĩnh/thẻ chữ, hoàn toàn không tải comic, nằm trong các module mới độc lập.

```
                   [Người dùng nhập câu hỏi Q&A]
                                 │
                     [P2: Media Source Router]
                     (Gemini 2.5 Flash Lite)
                                 │
             ┌───────────────────┴───────────────────┐
      [Pure Screen + Batcave Fail]             [Comic hoặc Mixed]
             │                                       │
     ▼ Mode: screen_qa                       ▼ Mode: comic_qa
  - Module: screen_pipeline.py            - Module: answer_pipeline.py
  - Research: screen_research.py          - Nghiên cứu từ Comic Canon
  - Narration: screen_script.py           - Narration: Issue # (Year)
  - Visuals: screen_shots.py              - Visuals: Panels Comic gốc
  - Fallback: Clip -> Card                - [P1: Flag ENABLE_VIDEO_CLIPS]
                                                 │
                                         ┌───────┴───────┐
                                      [OFF]            [ON]
                                         │               │
                                    100% Panel      Chọn Clip MP4
                                    (Hiện tại)      thay thế per-beat
```

### Bảng Cấu Hình & Feature Flags

| Tên Flag / Biến Môi Trường | Mặc định Code | Mặc định Thực Tế | Phạm vi ảnh hưởng | File & Dòng tham chiếu |
|---|---|---|---|---|
| `ENABLE_VIDEO_CLIPS` | `False` | `False` (`0`) | Bật/tắt toàn bộ layer MP4 trong UI và Stage 4/5 | `config.py:551` |
| `CLIP_SPEED_MIN` | `0.8` | `0.8` | Tốc độ chậm tối đa cho phép khi fit clip | `config.py:552`, `stages/stage_5/clips.py` |
| `CLIP_SPEED_MAX` | `1.25` | `1.25` | Tốc độ nhanh tối đa cho phép khi fit clip | `config.py:553`, `stages/stage_5/clips.py` |
| `CLIP_MAX_HOLD` | `0.3` | `0.3` (giây) | Thời lượng giữ frame cuối tối đa trước khi mở rộng | `config.py:554`, `stages/stage_5/clips.py` |
| `CHATTERBOX_SEED` | `None` (khi flag OFF)<br>`42` (khi flag ON) | `None` khi OFF<br>`42` khi ON | Seed cho Chatterbox TTS. **CHỈ SEED KHI FLAG ON**; khi OFF giữ nguyên unseeded TTS | `config.py:556`, `stage_4/_chatterbox_worker.py:27` |
| `POST_ATEMPO` | `1.30` | `1.15` (từ `.env`) | **KHÔNG ĐỔI MẶC ĐỊNH 1.30 TRONG CODE**. Đọc giá trị hiệu dụng từ file `.env` | `config.py`, `.env`, `stage_4/pipeline.py:165` |

---

## 3. P0: Sửa Lỗi Whip-Bridge Transition (Bằng Phản Chiếu Mirroring)

### Vấn Đề Kỹ Thuật & Giải Pháp Mirroring
- **Vị trí**: `stages/stage_5/pipeline.py:706-720` trong hàm `_shift_up` và `_shift_from_below`.
- **Nguyên nhân cũ**: Khi dịch chuyển `px`, code cũ crop 1 hàng pixel đáy và kéo giãn (`resize((OUTPUT_W, px))`), tạo ra các dải sọc đứng thô thiển (vertical streaks).
- **Quy tắc sửa dứt điểm (Fix 6)**: **TUYỆT ĐỐI KHÔNG DÙNG STRETCHING**. Lấp khoảng trống bằng cơ chế **PHẢN CHIẾU ĐỐI XỨNG (MIRRORING / REFLECT)** dải biên (`ImageOps.flip` / đối xứng qua trục ngang).

```python
# stages/stage_5/pipeline.py:706-720
def _shift_up(img: Image.Image, px: int) -> Image.Image:
    canvas = Image.new("RGB", (OUTPUT_W, OUTPUT_H))
    canvas.paste(img, (0, -px))
    if px > 0:
        # Lấy dải nội dung biên có chiều cao px ngay trên đáy, lật đối xứng gương để lấp khoảng trống
        sample_h = min(px, OUTPUT_H)
        sample = img.crop((0, OUTPUT_H - sample_h, OUTPUT_W, OUTPUT_H))
        reflected = sample.transpose(Image.FLIP_TOP_BOTTOM)
        # Nếu px > sample_h, lặp lại hoặc bù canvas
        canvas.paste(reflected, (0, OUTPUT_H - px))
    return canvas

def _shift_from_below(img: Image.Image, px: int) -> Image.Image:
    canvas = Image.new("RGB", (OUTPUT_W, OUTPUT_H))
    canvas.paste(img, (0, px))
    if px > 0:
        sample_h = min(px, OUTPUT_H)
        sample = img.crop((0, 0, OUTPUT_W, sample_h))
        reflected = sample.transpose(Image.FLIP_TOP_BOTTOM)
        canvas.paste(reflected, (0, 0))
    return canvas
```
- **Kiểm thử**: Viết test `tests/test_whip_bridge.py` đảm bảo không có dải pixel bị kéo giãn hoặc lặp lại 1D đơn điệu (`test_shift_up_no_vertical_streaks`, `test_whip_bridge_duration_and_audio_sync_preserved`).
- **P0 Baseline (Fix 7)**: Ngay sau khi sửa whip fix, chạy fixture chuẩn và thu thập giá trị băm baseline SHA256 của `shots.json` và `video_silent.mp4` lưu vào `tests/fixtures/p0_baseline.json`. Do thuật toán chọn whip có seed xác định theo `project:scene` (`pipeline.py:609-623`), baseline này có tính tất định 100%.

---

## 4. P1: Lớp Video Clip Hybrid Trong Comic Q&A

### 4.1. Kiến Trúc Giao Diện UI (--lan) & Điều Kiện Bọc FastAPI (Fix 3)

- **Quy tắc kích hoạt (Fix 3)**:
  - Khi `ENABLE_VIDEO_CLIPS=0`: Đường chạy `--lan` **BẮT BUỘC DÙNG CHÍNH XÁC PHƯƠNG THỨC CŨ** `ft.run(main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=args.port, assets_dir=str(PROJECTS_ROOT))` tại `ui/__main__.py:87`. Hoàn toàn không nạp FastAPI hay Uvicorn.
  - Khi `ENABLE_VIDEO_CLIPS=1`: Mới chuyển sang bọc Flet ASGI bên trong FastAPI trên cổng `8550`, nạp các custom route clip trước khi gọi `app.mount("/", flet_asgi)`. Desktop mode (`ui/__main__.py:67`) luôn giữ nguyên.

```python
# ui/__main__.py:83-98
if not config.ENABLE_VIDEO_CLIPS:
    # 100% đường chạy cũ ổn định
    ft.run(main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=args.port,
           assets_dir=str(PROJECTS_ROOT))
else:
    # Bọc FastAPI chỉ khi bật flag video clips
    from fastapi import FastAPI
    import uvicorn
    from .web_routes import router as clip_router
    flet_asgi = ft.run(main, export_asgi_app=True, assets_dir=str(PROJECTS_ROOT))
    app = FastAPI()
    app.include_router(clip_router)
    app.mount("/", flet_asgi)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
```

### 4.2. Trải Nghiệm Chọn Clip & Xóa Sạch Toàn Bộ Lựa Chọn (Master Decision a & Fix 5)

- **Nút "Dùng MP4"**: Xuất hiện trên từng beat card của `ui/screens/s_review_gate.py` khi `ENABLE_VIDEO_CLIPS=1`. Bấm nút kích hoạt `page.launch_url(f"/moments_review?project={project}&beat={beat_key}")` mở tab mới.
- **Tab Giao Diện Chọn Khoảnh Khắc (`/moments_review`)**:
  - Nhúng YouTube IFrame API (`enablejsapi=1`).
  - Gợi ý các mốc thời gian: Chapters, Subtitle lines, Most-replayed heatmap peaks.
  - Luôn có link dự phòng **"Xem trực tiếp trên YouTube tại mốc t"** (xử lý trường hợp video chặn nhúng ngoài trình duyệt).
  - Nút **"Dùng từ đây"**: Gửi POST `{project, beat, video_id, start}` về `/api/pick_moment`, cập nhật Flet page tức thì qua pubsub.
- **Quy Tắc Reset Toàn Bộ (Master Decision a & Fix 5)**:
  - Khi `ENABLE_VIDEO_CLIPS=1`: **Bất kỳ thao tác chỉnh sửa nào vào kịch bản Narration (sửa chữ, thêm/xóa/gộp/tách fragment) sẽ TỰ ĐỘNG XÓA SẠCH TOÀN BỘ (ALL) panel locks VÀ MP4 selections của TOÀN BỘ PROJECT** (`_clear_all_locks_and_clips()`).
  - Toàn bộ `review/locks.json` và `review/clips/clips.json` bị làm trống để tránh lệch nhịp timing.
  - Riêng Background TTS vẫn tối ưu: chỉ tổng hợp lại các câu bị thay đổi nội dung.
  - Khi `ENABLE_VIDEO_CLIPS=0`: Giữ nguyên hành vi cũ (không xóa lock toàn project).

### 4.3. Tải Section yt-dlp Với Công Thức Biên Chính Xác (Fix 9)

- Khi tải clip section, điểm kết thúc được tính toán chuẩn xác có tính đến hệ số tăng tốc tối đa:
  $$\text{end} = \text{start} + \text{beat\_duration} \times \text{CLIP\_SPEED\_MAX} + \text{margin}$$
  (Với `margin = 2.0s`, `CLIP_SPEED_MAX = 1.25`).
- Lệnh tải:
  `yt-dlp --no-playlist --download-sections "*start-end" --force-keyframes-at-cuts ...`
- Render bản xem trước 9:16 qua hàm `render_clip_preview` trong `stages/stage_5/clips.py` trước khi lưu vào manifest.

### 4.4. Thuật Toán Căn Khớp Thời Lượng Tại Render (Stage 5 Fit Math & Fix 10)

Hàm `_fit_clip_timing` trong `stages/stage_5/clips.py` thực hiện:
1. **Trim**: Cắt từ điểm `start` đã chọn.
2. **Extend Window**: Mở rộng cửa sổ vào file nguồn nếu còn dữ liệu.
3. **Speed Co Giãn**: Tính hệ số tốc độ trong dải an toàn `[CLIP_SPEED_MIN, CLIP_SPEED_MAX]` (0.8 - 1.25x).
4. **Hold Freeze Tail**: Nếu vẫn còn thiếu sau khi chỉnh speed, giữ frame cuối cùng $\le 0.3\text{s}$ (`CLIP_MAX_HOLD`).
5. **Hard Cut Tuyệt Đối**: Ép **HARD CUT** tại biên nối trước và sau clip shot trong `stages/stage_5/pipeline.py`; các shot panel comic giữ nguyên dissolve (`XFADE_DURATION=0.25`).

---

## 5. Hệ Thống Background TTS & Đồng Bộ Beat Timing

### 5.1. Quy Tắc Hoạt Động & Cơ Chế Không Chặn (Fix 8)
1. **File Thiết Lập Seed**: Liệt kê rõ file `stages/stage_4/_chatterbox_worker.py` (nơi gán `torch.manual_seed`, `np.random.seed`, `random.seed` theo `job.get("seed")`).
2. **Không Gọi `ensure_reviewed`**: Tiến trình background TTS chạy nền **TUYỆT ĐỐI KHÔNG GỌI `ensure_reviewed()`** (vì review gate đang mở và cố tình chặn Stage 4 tại `stages/stage_4/pipeline.py:151`).
3. **Tái Sử Dụng Chunk Cache**:
   - Khi Stage 4 pipeline chạy chính thức, nạp các file chunk WAV đã cache trong `projects/<p>/cache/tts/<sha256>.wav`, chỉ tổng hợp những chunk còn thiếu.
   - Dùng chính xác thuật toán chia câu `_chunks` ($\le 320$ chars, `chatterbox_tts.py:87-103`), `_even_words` (`chatterbox_tts.py:104-113`) và tốc độ `POST_ATEMPO = 1.15` từ `.env`.
4. **Tính Toán Beat Window**:
   - Tỷ lệ từ ngữ từ câu sang beat (`_even_words`).
   - Gap absorption Stage 5 (`shots.py:797-803`).
   - Gộp shot ngắn $< 1.5\text{s}$ (`QA_MIN_SHOT_SECONDS`).
   - Khấu trừ $0.12\text{s}$ mỗi bên nếu có whip transition (`pipeline.py:631-649`).
5. **Windows Keep-Awake**: Gọi API `ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)` ngăn Windows sleep trong suốt quá trình chạy TTS.
6. **Priority Queue**: Khi Master click vào một beat, câu chứa beat đó được đưa ngay lên đầu hàng đợi sinh TTS.

---

## 6. P2: Type-Safe Media Source Router

- **Model & Chi Phí**: `google/gemini-2.5-flash-lite`, $0.00031/Q, 2.50s latency, schema validation lỗi 0.0%.
- **Schema Pydantic**: `PrimaryMedium` (`comic`, `film`, `tv_animation`, `game`, `mixed`), `VisualSource` (`comic`, `youtube`, `both`), `RoutedItem`, `QuestionRouteResponse`.
- **Quy Tắc Định Tuyến Deterministic**:
  $$\text{Route} = \begin{cases} 
  \text{screen\_qa} & \text{nếu } (\forall i, i.\text{medium} \notin \{\text{comic}, \text{mixed}\}) \land (\neg \text{BatcaveIssueExists}) \land (\text{ClipsFound}) \\
  \text{comic\_qa} & \text{ngược lại (ưu tiên an toàn cho Comic)}
  \end{cases}$$
- **Kiểm Tra Batcave Sâu 4 Bước (Issue-Level Verifier)**:
  1. Disambiguation theo năm series.
  2. Khám phá `window.__DATA__.chapters`.
  3. Khớp chính xác số issue.
  4. Ping kiểm tra `getChapterData`.
- **Lưới An Toàn**: Tránh nhận nhầm câu hỏi kinh điển có cả truyện và phim (như Civil War) thành screen media.

---

## 7. P3: Chế Độ Độc Lập `screen_qa` (NEW MODULES ONLY - Fix 4 & 11)

Để bảo toàn tuyệt đối 100% đường chạy Comic ổn định, chế độ `screen_qa` được triển khai **HOÀN TOÀN TRONG CÁC MODULE MỚI**:

| Chức năng | Module Comic Cũ (KHÔNG CHẠM VÀO) | Module Screen Mới (NEW MODULE ONLY) | File Mới |
|---|---|---|---|
| **CLI Runner** | `stages/answer_pipeline.py` | `stages/screen_pipeline.py` (CLI chạy toàn bộ luồng Screen Q&A) | `stages/screen_pipeline.py` |
| **Research** | `stages/stage_1/answer_research.py` | `stages/stage_1/screen_research.py` (Nghiên cứu từ Screen Fandom wikis, không đụng tới `:632`) | `stages/stage_1/screen_research.py` |
| **Narration Script** | `stages/stage_3/write_script.py` | `stages/stage_3/screen_script.py` (Prompt & format chuẩn trích dẫn `Title (Year)`) | `stages/stage_3/screen_script.py` |
| **Shot Builder & Render** | `stages/stage_5/shots.py` (`render_shot`, `_build_shots_for_qa_locked`) | `stages/stage_5/screen_shots.py` (Dựng shot không phụ thuộc panel count, quản lý chuỗi fallback không crash) | `stages/stage_5/screen_shots.py` |

### Chuỗi Fallback 4 Cấp Độ Không Bao Giờ Crash (Fix 11)
Trong `stages/stage_5/screen_shots.py`:
```
[1. Video Clip chính]
       │ (lỗi tải / codec / contract)
       ▼
[2. Video Clip dự phòng (Backup candidate)]
       │ (không có clip thay thế)
       ▼
[3. Ảnh tĩnh HD / Custom Image (Ken Burns chuyển động)]
       │ (không có ảnh chụp cảnh)
       ▼
[4. Thẻ chữ đồ họa (Text Card - utils/text_card.py)]
```
Thẻ chữ đồ họa render bằng Pillow qua `utils/text_card.py`, không bao giờ để xảy ra `RuntimeError` do thiếu `source_image`.

---

## 8. Quy Trình Bàn Giao Option A & Phân Chia Nhân Sự (Worker Allocation)

### 8.1. Phân Chia Công Việc Đa Luồng (Worker Allocation)
1. **`video-qa/p1-verify`**: Chịu trách nhiệm review P1, hoàn tất bộ test hồi quy hybrid và chạy E2E Windows Server cho P1.
2. **`video-qa/p3-core`**: Chịu trách nhiệm khởi tạo các module P3 mới: CLI (`stages/screen_pipeline.py`), research (`stages/stage_1/screen_research.py`), narration (`stages/stage_3/screen_script.py`).
3. **`video-qa/p3-visual`**: Chịu trách nhiệm xây dựng shot builder P3 (`stages/stage_5/screen_shots.py`), chuỗi fallback không crash và giao diện Screen UI.
4. **`comic-book-pipeline-12`**: Chịu trách nhiệm hoàn tất P2 (router rules, Batcave verifier) và giữ vai trò **Integrator**: hợp nhất các nhánh của các worker trên `feat/video-qa-hybrid`.

### 8.2. Quy Trình Bàn Giao Option A (Gated Delivery)
1. Các worker hoàn thành task trên nhánh riêng, Integrator merge vào `feat/video-qa-hybrid`.
2. Chạy full suite kiểm thử trên checkout phụ `D:\code\cbp-video-test-int` qua SSH tunnel trên cổng `8562`.
3. Push nhánh `feat/video-qa-hybrid`.
4. **DỪNG LẠI CHỜ MASTER DUYỆT (Gated Checkpoint)**.
5. **CHỈ SAU KHI MASTER ĐỒNG Ý RÕ RÀNG**: Integrator pull code vào `D:\code\comic-book-pipeline`, khởi động lại giao diện UI trên cổng 8550 với flag `ENABLE_VIDEO_CLIPS=1` để demo cho Master.
