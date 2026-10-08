# SPEC: Tích Hợp Video Clips Vào Pipeline Q&A (Comic Q&A Hybrid & Screen Q&A)

| Tài liệu | Trạng thái | Ngày tạo | Phiên bản | Tác giả |
|---|---|---|---|---|
| `todo/video-clip-feature/2026-10-07-video-qa-spec.md` | Bản thiết kế hoàn chỉnh (Master Reviewed) | 2026-10-07 | 1.0 | Antigravity |

---

## 1. Mục Tiêu & Nguyên Tắc Thiết Kế

1. **Tổng quát hóa (General Mechanism)**: Tuyệt đối không hard-code tên truyện, số trang, nhân vật, số issue hay hằng số cục bộ. Mọi cơ chế (xử lý khung hình, fit timing, fallback, routing) phải áp dụng được cho mọi comic và video.
2. **Bảo toàn nhánh ổn định (Zero-Regression & Byte-Identical)**:
   - Toàn bộ tính năng video clip trong Comic Q&A nằm sau feature flag (`ENABLE_VIDEO_CLIPS=0` mặc định OFF).
   - Khi flag OFF hoặc project không có `review/clips/clips.json`: đường chạy Comic Q&A (`explore_answer`) cho ra `shots.json` và `video_silent.mp4` trùng khớp SHA256 100% với phiên bản hiện tại.
   - Chế độ thuần video `screen_qa` là một pipeline mode riêng biệt (`PipelineMode.SCREEN_QA`), không dùng chung luồng tải truyện của Stage 1.
3. **Môi trường triển khai tách biệt (Windows Server Isolation)**:
   - Quá trình chạy thử nghiệm chỉ thực hiện trên thư mục clone phụ trên Windows Server (ví dụ `D:\code\cbp-video-test`) qua SSH tunnel.
   - Tuyệt đối không can thiệp thư mục production `D:\code\comic-book-pipeline` và không đụng vào firewall Windows.

---

## 2. Kiến Trúc Tổng Thể & Feature Flags

Hệ thống bổ sung 2 luồng phục vụ video clips:
- **P1 - Comic Q&A Hybrid**: Truyện tranh vẫn là nguồn hình ảnh gốc. Master có thể chọn thay thế panel Ken Burns của từng beat bằng video clip ngắn (tắt tiếng, căn 9:16).
- **P3 - Screen Q&A**: Chế độ độc lập dành riêng cho các câu hỏi điện ảnh/hoạt hình (MCU, DCAU...). Hình ảnh 100% là video clip/hình tĩnh/thẻ chữ, hoàn toàn không tải comic.

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
  - Nghiên cứu từ Screen Wikis            - Nghiên cứu từ Comic Canon
  - Narration: Movie (Year)               - Narration: Issue # (Year)
  - Visuals: 100% Clips / Text Card       - Visuals: Panels Comic gốc
  - Fallback: Clip -> Card                - [P1: Flag ENABLE_VIDEO_CLIPS]
                                                 │
                                         ┌───────┴───────┐
                                      [OFF]            [ON]
                                         │               │
                                    100% Panel      Chọn Clip MP4
                                    (Hiện tại)      thay thế per-beat
```

### Bảng Cấu Hình & Feature Flags

| Tên Flag / Biến Môi Trường | Mặc định | Phạm vi ảnh hưởng | File & Dòng tham chiếu |
|---|---|---|---|
| `ENABLE_VIDEO_CLIPS` | `0` (OFF) | Bật/tắt toàn bộ layer MP4 trong UI và Stage 4/5 | `config.py`, `ui/screens/s_review_gate.py` |
| `CLIP_SPEED_MIN` | `0.8` | Tốc độ chậm tối đa cho phép khi fit clip | `stages/stage_5/clips.py` |
| `CLIP_SPEED_MAX` | `1.25` | Tốc độ nhanh tối đa cho phép khi fit clip | `stages/stage_5/clips.py` |
| `CLIP_MAX_HOLD` | `0.3` (giây) | Thời lượng giữ frame cuối tối đa trước khi mở rộng | `stages/stage_5/clips.py` |
| `CHATTERBOX_SEED` | `42` | Seed cố định cho bộ sinh giọng nói Chatterbox TTS | `stages/stage_4/chatterbox_tts.py:173` |
| `POST_ATEMPO` | `1.15` | Tốc độ đọc TTS cho Q&A (lấy từ `.env`) | `.env`, `stages/stage_4/pipeline.py:142` |

---

## 3. P0: Sửa Lỗi Whip-Bridge Transition

### Vấn Đề Kỹ Thuật
- **Vị trí**: `stages/stage_5/pipeline.py:706-712` trong hàm `_shift_up` (và đối xứng `_shift_from_below` tại `pipeline.py:714-720`).
- **Nguyên nhân**: Khi khung hình dịch chuyển lên `px` pixel, phần hở phía đáy hiện đang lấy đúng 1 hàng pixel cuối cùng của ảnh gốc và kéo giãn toàn bộ (`img.crop((0, OUTPUT_H - 1, OUTPUT_W, OUTPUT_H)).resize((OUTPUT_W, px))`).
- **Hệ quả**: Tạo ra các dải sọc đứng kéo dài (vertical streaks / clamping artifacts) rất thô tại điểm nối chuyển cảnh từ Video Clip sang Panel Ken Burns.

### Thiết Kế Sửa Đổi
```python
# stages/stage_5/pipeline.py:706-720
def _shift_up(img: Image.Image, px: int) -> Image.Image:
    canvas = Image.new("RGB", (OUTPUT_W, OUTPUT_H), (0, 0, 0))
    canvas.paste(img, (0, -px))
    if px > 0:
        # Thay vì stretch 1px row gây streak:
        # Lấy dải nội dung đáy thực tế tương ứng với px (tối đa lấy phần biên) và áp dụng mờ chuyển tiếp
        sample_h = min(px, 32)
        sample = img.crop((0, OUTPUT_H - sample_h, OUTPUT_W, OUTPUT_H))
        edge = sample.resize((OUTPUT_W, px), Image.BILINEAR)
        canvas.paste(edge, (0, OUTPUT_H - px))
    return canvas

def _shift_from_below(img: Image.Image, px: int) -> Image.Image:
    canvas = Image.new("RGB", (OUTPUT_W, OUTPUT_H), (0, 0, 0))
    canvas.paste(img, (0, px))
    if px > 0:
        sample_h = min(px, 32)
        sample = img.crop((0, 0, OUTPUT_W, sample_h))
        edge = sample.resize((OUTPUT_W, px), Image.BILINEAR)
        canvas.paste(edge, (0, 0))
    return canvas
```
- **Kiểm thử**: Viết test `tests/test_whip_bridge.py` kiểm tra độ biến thiên gradient của vùng biên (`px > 0`), đảm bảo không có ma trận cột trùng lặp 1D (streak-free).

---

## 4. P1: Lớp Video Clip Hybrid Trong Comic Q&A

### 4.1. Kiến Trúc Giao Diện UI & Fast-API ASGI Mount

- **Bối cảnh**: Master sử dụng giao diện duyệt qua mạng LAN (`--lan`). Flet web render trên nền canvas WebGL/HTML5 nên không thể nhúng iframe YouTube trực tiếp (bị chặn tương tác, CORS và giới hạn API player).
- **Giải pháp**:
  - Giữ nguyên desktop `ft.run(main)` tại `ui/__main__.py:67`.
  - Tại đường dẫn `--lan` (`ui/__main__.py:87`), tích hợp Flet như một ASGI application bên trong FastAPI trên cổng `8550`.
  - **Quy tắc bắt buộc**: Mọi Custom Routes của FastAPI (`/moments_review`, `/api/pick_moment`, `/api/beat_tts_status`) phải khai báo **TRƯỚC** khi gọi `app.mount("/", flet_asgi)`. Nếu mount trước, Flet catch-all router sẽ nuốt toàn bộ HTTP requests.

```python
# ui/__main__.py:87
from fastapi import FastAPI
import uvicorn
from .web_routes import router as clip_router

flet_asgi = ft.run(main, export_asgi_app=True, assets_dir=str(PROJECTS_ROOT))
app = FastAPI()
app.include_router(clip_router)   # Đăng ký TRƯỚC
app.mount("/", flet_asgi)         # Catch-all mount SAU CÙNG

uvicorn.run(app, host="0.0.0.0", port=args.port)
```

### 4.2. Trải Nghiệm Chọn Clip (Review Gate Screen)

- **Vị trí**: `ui/screens/s_review_gate.py` trong hàm `_beat_card()`.
- **Nút "Dùng MP4"**:
  - Đặt tại thanh công cụ của mỗi beat card bên cạnh icon thêm ảnh custom (`s_review_gate.py:1474-1496`).
  - Khi click: kích hoạt `page.launch_url(f"/moments_review?project={project}&beat={beat_key}")` mở tab trình duyệt mới độc lập.
- **Tab Giao Diện Chọn Khoảnh Khắc (`/moments_review`)**:
  - Trình bày danh sách video lấy từ `clip_fetch moment`.
  - Mỗi video nhúng YouTube IFrame API (`https://www.youtube.com/embed/{video_id}?enablejsapi=1`).
  - Tự động hiển thị các nút mốc thời gian gợi ý: Chapters, Subtitle lines khớp mô tả, Most-replayed heatmap peaks.
  - Luôn hiển thị đường link dự phòng: **"Xem trực tiếp trên YouTube tại mốc t"** (Xử lý thực tế: ~3/18 video YouTube chặn cờ nhúng ngoài trình duyệt `playable_in_embed=False`).
  - Nút **"Dùng từ đây"**: Đọc mốc thời gian chính xác qua `player.getCurrentTime()`, gửi HTTP POST `{project, beat, video_id, start}` về `/api/pick_moment`.
- **Đồng bộ thời gian thực**: Flet web lắng nghe thông báo qua cơ chế pubsub nội bộ (`page.pubsub.subscribe`), cập nhật ngay trạng thái beat card từ "Panel" sang "MP4: {video_id} @ {start}s".

### 4.3. Tìm Kiếm Song Song & Tải Cắt Đoạn Chính Xác

| Hạng mục | Cơ chế kỹ thuật | File & Hàm tham chiếu | Lợi ích đo lường |
|---|---|---|---|
| **Query Sinh Ra** | Lấy từ `answer_context.items`: `{entity} + {drawable_moment}`. Cho phép Master sửa trực tiếp trên giao diện tab review. | `stages/stage_1/answer_research.py:591-606` | Trúng ngữ cảnh visual của phân cảnh Q&A |
| **Song Song Hóa** | Chạy đa luồng song song qua `ThreadPoolExecutor(max_workers=8)` khi tìm kiếm moment và bóc tách metadata. | `stages/clip_fetch.py:moment_search` | Giảm thời gian tìm kiếm từ ~30-40s xuống 7-13s (~4.3× nhanh hơn) |
| **Bộ Nhớ Đệm** | Cache kết quả You.com và YouTube metadata theo `(beat_key, query)` và `video_id`. | `stages/clip_fetch.py` | Tránh gọi trùng lặp, mở lại tab không phải chờ |
| **Tải Section** | Dùng cú pháp: `yt-dlp --download-sections "*S-E" --force-keyframes-at-cuts` (trong đó $S = \text{start}$, $E = \text{start} + \text{beat\_duration} + 2.0$). | `stages/clip_fetch.py:fetch_clip_section` | File tải nhỏ hơn **14–33×** so với tải cả video; frame đầu cắt chính xác tại keyframe |
| **Preview 9:16** | Sau khi tải xong đoạn cắt, render nhanh bản preview 9:16 đúng độ dài beat và phát thử trên giao diện trước khi chốt lock. | `stages/stage_5/clips.py:render_clip_preview` | Master thẩm định được góc crop và nhịp hình trước khi render toàn bộ video |
| **Rollback** | Nút "Hoàn tác về Panel" / "Chọn kết quả cache khác" / "Tìm kiếm lại" ngay trên beat card. | `ui/screens/s_review_gate.py` | Linh hoạt, không sợ mất lựa chọn panel cũ |

### 4.4. Quy Tắc Khớp Nhân Vật & Tắt Tiếng
- Clip chỉ cần đảm bảo **đúng nhân vật** (`entity`) và thể hiện hành động liên quan tới câu trả lời.
- Toàn bộ âm thanh của clip bị tắt hoàn toàn (`-an`), chỉ lấy hình ảnh chuyển động làm nền minh họa cho giọng đọc narration của Q&A.

### 4.5. Thuật Toán Căn Khớp Thời Lượng Tại Render (Stage 5 Fit Math)

Stage 5 tính toán việc căn khớp dựa trên thời lượng audio THỰC TẾ của beat, tuyệt đối **không bake cứng thời lượng lúc người dùng bấm chọn clip**.

Thứ tự ưu tiên xử lý:
1. **Trim**: Cắt video từ mốc $S$ với độ dài bằng độ dài beat $T_{\text{shot}}$.
2. **Extend Window**: Nếu clip gốc có sẵn dữ liệu trước/sau mốc chọn, mở rộng cửa sổ vào file nguồn để giữ trọn hành động.
3. **Speed Adjustment**: Áp dụng thay đổi tốc độ video trong dải an toàn $[0.8\times, 1.25\times]$ thông qua filter `setpts=(1/SPEED)*PTS`.
   - Nếu clip ngắn hơn beat: giảm tốc độ (tối đa xuống $0.8\times$).
   - Nếu clip dài hơn một chút: tăng tốc độ (tối đa lên $1.25\times$).
4. **Hold Freeze Tail**: Nếu sau khi co giãn tốc độ vẫn còn thiếu thời gian, giữ frame cuối cùng (`tpad=stop_mode=clone`) với thời lượng $\le 0.3\text{s}$.
5. **Chuyển cảnh (Transitions)**:
   - Các shot sử dụng Video Clip: Bắt buộc dùng **HARD CUT** cả trước và sau clip.
   - Các shot sử dụng Comic Panel: Tiếp tục giữ hiệu ứng hòa tan **DISSOLVE** (`XFADE_DURATION=0.25s`, `config.py:359`).

---

## 5. Hệ Thống Background TTS & Đồng Bộ Beat Timing

### 5.1. Khái Toán Thời Lượng Beat Phục Vụ Chọn Clip
- Để Master có thể chọn clip vừa khít với từng beat trong lúc duyệt Review Gate, hệ thống phải biết trước thời lượng của beat.
- Khi bật cờ `ENABLE_VIDEO_CLIPS=1`, việc mở màn hình Select-Beat sẽ kích hoạt tiến trình **Background TTS** toàn bộ kịch bản narration.

### 5.2. Đồng Nhất Mô Hình & Caching Bit-Identical
- **Chia đoạn (Chunking)**: Dùng chính xác thuật toán chia câu của Stage 4 (`stages/stage_4/chatterbox_tts.py:69, 87-103`): độ dài câu $\le 320$ ký tự, ngắt câu theo dấu chấm/phẩy.
- **Fixed Seed**:
  - Hiện tại Chatterbox TTS không cố định seed và chạy với `temperature = 0.8`.
  - Bổ sung cấu hình `CHATTERBOX_SEED = 42`. Trên cùng một máy, fixed seed đảm bảo file WAV đầu ra bit-identical.
  - *Lưu ý sai lệch phần cứng*: Mac MPS và Windows CPU có thể lệch nhau tới 0.45s/beat dù cùng seed. Do đó, **file WAV sinh ra và cache trên Windows Server là chân lý duy nhất (Single Source of Truth)**.
- **Khóa Cache Độc Nhất**:
  $$\text{CacheKey} = \text{sha256}(\text{sentence\_text} + \text{voice\_id} + \text{exaggeration} + \text{cfg\_weight} + \text{seed} + \text{post\_atempo})$$
- **Tái sử dụng ở Stage 4**: Stage 4 khi chạy pipeline chính (`stages/stage_4/pipeline.py:151`) sẽ kiểm tra thư mục cache WAV, nạp lại toàn bộ audio đã sinh ở background mà không phải tổng hợp lại.
- **Pacing**: Cấu hình `POST_ATEMPO = 1.15` đọc từ file `.env` (thay vì giá trị mặc định 1.30 trong code).

### 5.3. Công Thức Tính Cửa Sổ Beat (Beat Window Calculation)

Thời lượng của một beat được tính toán kết hợp các quy tắc sau:
1. **Phân bổ từ câu**:
   $$T_{\text{beat}} = T_{\text{sentence}} \times \frac{N_{\text{words\_in\_beat}}}{N_{\text{words\_in\_sentence}}}$$
   (Sử dụng hàm `_even_words` trong `chatterbox_tts.py:104-113`).
2. **Gap Absorption**: Hấp thụ khoảng lặng giữa các chunk theo quy tắc Stage 5 (`stages/stage_5/shots.py:797-803`).
3. **Ngưỡng tối thiểu**: Độ dài beat tối thiểu đạt $0.4\text{s}$.
4. **Hợp nhất shot ngắn**: Nếu beat nhỏ hơn `QA_MIN_SHOT_SECONDS = 1.5s` (`stages/stage_5/shots.py:1065, ~2009`), hợp nhất vào shot kề trong cùng phân cảnh.
5. **Bù trừ Whip Transition**: Nếu giữa 2 cảnh có whip transition, mỗi bên bị khấu trừ $0.12\text{s}$ (`stages/stage_5/pipeline.py:631-649`).

### 5.4. Tối Ưu Hóa Hiệu Năng Trên Windows Server
- CPU Windows xử lý Chatterbox tốn khoảng ~22s cho mỗi câu $\rightarrow$ Toàn bộ video Short (60–76s) cần khoảng 5–7 phút tổng hợp.
- **Cập nhật lũy tiến (Progressive UI Update)**: Giao diện hiển thị thời lượng ngay khi từng câu hoàn thành.
- **Ưu tiên hàng đợi (Priority Queue)**: Khi Master click chọn vào bất kỳ beat nào trên giao diện, câu chứa beat đó sẽ được đẩy ngay lên đầu hàng đợi tổng hợp.
- **Chống ngủ Windows (Server Keep-Awake)**: Gọi API Windows `ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)` trong suốt thời gian job chạy nền để ngăn máy rơi vào chế độ sleep sau 30-60s nhàn rỗi.

### 5.5. Cơ Chế Invalidation Khi Sửa Kịch Bản
- **Khi `ENABLE_VIDEO_CLIPS=1`**: Nếu Master sửa text của một câu narration, hệ thống **tự động xóa sạch lựa chọn Panel và MP4 của các beat thuộc câu đó**, đồng thời đưa riêng câu đó vào hàng đợi re-synthesize.
- **Khi `ENABLE_VIDEO_CLIPS=0`**: Giữ nguyên hành vi hiện tại của pipeline (không xóa lock, không chạy nền TTS).

---

## 6. P2: Type-Safe Media Source Router

### 6.1. Dữ Liệu Thực Nghiệm (Spike Evidence)
Từ kết quả đo đạc tại `/tmp/source_router/FINAL_REPORT.md` và `run_part3.py`:
- Mô hình: `google/gemini-2.5-flash-lite`.
- Chi phí: **$0.00031 / câu hỏi**, độ trễ trung bình **2.50s**.
- Tỷ lệ lỗi schema Pydantic: **0.0%** (0 retries).
- Độ chính xác phân loại độc lập: **90.0%**.

### 6.2. Pydantic Schema Xác Thực

```python
from enum import Enum
from pydantic import BaseModel, Field

class PrimaryMedium(str, Enum):
    COMIC = "comic"
    FILM = "film"
    TV_ANIMATION = "tv_animation"
    GAME = "game"
    MIXED = "mixed"

class VisualSource(str, Enum):
    COMIC = "comic"
    YOUTUBE = "youtube"
    BOTH = "both"

class RoutedItem(BaseModel):
    event: str = Field(description="Sự kiện hoặc tình tiết câu hỏi đang xét")
    primary_medium: PrimaryMedium = Field(description="Phương tiện truyền thông gốc của tình tiết")
    visual_source: VisualSource = Field(description="Nguồn tài nguyên hình ảnh khuyến nghị")
    adaptation_title: str | None = Field(default=None, description="Tên tác phẩm và năm, ví dụ: 'Avengers: Endgame (2019)'")
    evidence_urls: list[str] = Field(description="Danh sách URL dẫn chứng từ kết quả tìm kiếm")
    confidence: float = Field(ge=0.0, le=1.0, description="Độ tin cậy từ 0.0 đến 1.0")
    reason: str = Field(description="Giải thích ngắn gọn lý do phân loại")

class QuestionRouteResponse(BaseModel):
    question: str
    items: list[RoutedItem]
```

### 6.3. Quy Tắc Định Tuyến Xác Định (Deterministic Python Rule)

Chính sách kênh ưu tiên Comic là cốt lõi:
Hệ thống **CHỈ CHUYỂN SANG `screen_qa`** khi thỏa mãn đồng thời cả 3 điều kiện:
1. **Không có yếu tố Comic**: Mọi item trong `items` đều KHÔNG thuộc `comic` hoặc `mixed`.
2. **Kiểm tra Batcave cấp Issue thất bại**: Không tìm thấy issue truyện tranh thực tế nào tồn tại trên Batcave.
3. **Tìm thấy Video Clip**: YouTube tìm kiếm được clip phù hợp và có thể nhúng/tải.

$$\text{Route} = \begin{cases} 
\text{screen\_qa} & \text{nếu } (\forall i, i.\text{medium} \notin \{\text{comic}, \text{mixed}\}) \land (\neg \text{BatcaveIssueExists}) \land (\text{ClipsFound}) \\
\text{comic\_qa} & \text{ngược lại (luôn an toàn với comic)}
\end{cases}$$

### 6.4. Kiểm Tra Sâu Batcave Cấp Issue (Issue-Level Batcave Check)
*Tránh lỗi dương tính giả khi chỉ tìm kiếm tên series*: Series "Amazing Spider-Man (2018)" không chứa issue #121 (vốn thuộc run 1963).
Quy trình kiểm tra 4 bước:
1. Phân giải series theo năm xuất bản (`series disambiguation by year`).
2. Khám phá danh sách issue từ web (`discover_issues` qua `window.__DATA__.chapters`).
3. Khớp chính xác số issue (`exact issue-number match`).
4. Ping kiểm tra trang truyện (`getChapterData`).

### 6.5. Tín Hiệu Phân Loại Mạnh Nhất
- **Screen**: Subdomain Fandom (`marvelcinematicuniverse.fandom.com`, `dcau.fandom.com`, `intothespiderverse.fandom.com`), Wikipedia có định danh `(film)`.
- **Comic**: Subdomain `marvel.fandom.com` có hậu tố `(Earth-616)`, Wikipedia có định danh `(comic)`.
- **Điểm yếu đã biết**: Câu hỏi *"Why did Captain America and Iron Man fight?"* có xu hướng bị LLM nhận định là phim Civil War (2016). Quy tắc (2) kiểm tra Batcave là lưới bảo vệ tuyệt đối giúp giữ lại câu hỏi cho `comic_qa`.

---

## 7. P3: Chế Độ Độc Lập `screen_qa`

### 7.1. Cô Lập Hoàn Toàn Tuyến Comic
- `stages/stage_1/answer_research.py:632` sẽ throw `ValueError` nếu thiếu `reader_url` của Batcave.
- Do đó, `screen_qa` tạo module riêng `stages/stage_1/screen_research.py`, hoàn toàn bỏ qua bước tải truyện.

### 7.2. Đặc Điểm Pipeline của Screen Q&A
1. **Nghiên cứu Canon**: Bóc tách nội dung từ Screen Wikis (MCU Fandom, DC Extended Universe Wiki...).
2. **Kịch bản Narration**: Stage 3 trích dẫn nguồn theo dạng `Tên Phim (Năm phát hành)` thay vì `Tên Truyện #Số (Năm)`.
3. **Bộ Dựng Shot Độc Lập (Shot Builder)**: Không sử dụng `_build_shots_for_qa_locked` (`stages/stage_5/shots.py:1740-2033`) vì hàm này phụ thuộc vào mảng panel truyện tranh (`_panel_pool`). Tạo hàm riêng `build_shots_for_screen_qa`.

### 7.3. Chuỗi Fallback An Toàn Tuyệt Đối (Không Bao Giờ Crash)
Hiện tại nếu clip lỗi và không có panel comic, pipeline sẽ crash với `RuntimeError` (`stages/stage_5/shots.py:3003-3006`).
Trong `screen_qa`, hệ thống thực thi chuỗi fallback 4 cấp độ:

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
Thẻ chữ đồ họa tạo bằng Pillow (`utils/text_card.py`), render khung chữ sắc nét trên nền tối mang logo kênh, đảm bảo video luôn hoàn thành 100% không bao giờ gián đoạn.

---

## 8. Ma Trận Tương Tác Hệ Thống & Kiểm Thử Hồi Quy

| Thành phần | File tác động | Chế độ Comic (Flag OFF) | Chế độ Comic (Flag ON) | Chế độ Screen Q&A |
|---|---|---|---|---|
| **Whip Transition** | `stages/stage_5/pipeline.py:706` | Sửa dải streak, mượt mà hơn | Sửa dải streak, mượt mà hơn | Không áp dụng whip |
| **FastAPI Launcher** | `ui/__main__.py:87` | Giữ nguyên desktop, thêm cổng LAN | Cổng LAN + routes chọn clip | Cổng LAN + routes chọn clip |
| **Review Gate UI** | `ui/screens/s_review_gate.py` | Ẩn nút "Dùng MP4" | Hiện nút "Dùng MP4", mở tab mới | Ẩn gallery comic, chỉ hiện clip |
| **TTS Synthesis** | `stages/stage_4/chatterbox_tts.py` | Seed mặc định, chạy tuần tự | Fixed seed 42, background queue | Fixed seed 42, background queue |
| **Shot Assembly** | `stages/stage_5/shots.py` | 100% Panel Ken Burns | Panel + Video Clip hybrid | 100% Clip / Still / Text Card |
| **Concat Stream** | `stages/stage_5/pipeline.py` | Stream copy `-c copy` | Stream copy `-c copy` (clip bit-match SPS) | Stream copy `-c copy` |
