# Shot video clip trong Stage 5: cách dùng (2026-10-06)

Tính năng **opt-in**: project nào có `review/clips/clips.json` thì một số beat sẽ phát clip video
(đã cắt in/out, tắt tiếng, đưa về khung 9:16) thay cho panel Ken Burns. Project không có manifest
render **byte-identical** như trước.

Code: `stages/stage_5/clips.py` (manifest, thứ tự ưu tiên, gắn clip vào shot, render) và
`stages/clip_fetch.py` (CLI tìm/tải/chọn clip). Test: `tests/test_video_clips.py`.

## 1. Quy trình nhanh

```bash
# 1) Tìm clip trên YouTube qua You.com Web Search (cần YDC_API_KEY trong env hoặc .env)
python -m stages.clip_fetch search "Green Lantern The Animated Series official clip DC Kids"

# 1b) Hoặc tìm theo KHOẢNH KHẮC: nhiều video, mỗi video vài cửa sổ thời gian kèm lý do
#     (chapter / phụ đề khớp mô tả / đỉnh "Most replayed"), không tải video
python -m stages.clip_fetch moment "Kilowog fights Hal Jordan"

# 2) Tải + transcode về projects/<p>/review/clips/<id>.mp4 + contact sheet <id>_sheet.jpg
python -m stages.clip_fetch fetch "https://www.youtube.com/watch?v=gKiT1ekWIAA" --project <p>

# 3) Xem kỹ một đoạn để chọn in/out (mỗi 0,5s từ giây 60 đến 80)
python -m stages.clip_fetch sheet projects/<p>/review/clips/gKiT1ekWIAA.mp4 --start 60 --end 80 --every 0.5

# 4) Xem beat key của narration
python -m stages.clip_fetch beats --project <p>

# 5) Gắn clip vào beat
python -m stages.clip_fetch add --project <p> --file review/clips/gKiT1ekWIAA.mp4 \
    --start 64.5 --end 67.0 --beat 3:1 --crop-cx 0.55

# 6) Render như bình thường
python -m stages.stage_5 --project <p> --force
```

Có thể ghi entry chỉ có `--url` (chưa tải). Stage 5 tự tải trước khi dựng shot, hoặc chạy
`python -m stages.clip_fetch sync --project <p>` để tải trước.

## 2. Manifest `review/clips/clips.json`

```json
{"clips": [
  {"id": "antimonitor-1",
   "file": "review/clips/gKiT1ekWIAA.mp4",
   "source_url": "https://www.youtube.com/watch?v=gKiT1ekWIAA",
   "start": 64.5, "end": 67.0,
   "beat": "3:1",
   "crop": {"cx": 0.55}}
]}
```

| Trường | Ý nghĩa |
|---|---|
| `file` | Đường dẫn tương đối theo project hoặc tuyệt đối. Bỏ trống được nếu có `source_url`. |
| `source_url` | Để ghi credit. Cũng là URL mà `sync` / Stage 5 dùng để tải khi chưa có `file`. |
| `start`, `end` | Giây trong file `<id>.mp4` đã cache (trùng timeline video gốc). `end` bỏ trống = phát đến hết shot. |
| `beat` | Key giống custom image: `intro`, `outro`, `<scene_id>`, `<scene_id>:<frag_idx>`. |
| `desc` | Chỉ dùng khi không có `beat`: đặt theo độ trùng từ, cùng cơ chế với custom image. |
| `crop` | Tuỳ chọn. `{"cx":0.6,"cy":0.5}` là tâm khung 9:16 lớn nhất (bám chủ thể). `{"x","y","w","h"}` là vùng cắt; mọi giá trị là tỉ lệ 0–1. |
| `id`, `enabled` | Tuỳ chọn. `enabled: false` = bỏ qua entry. |

Entry sai (thiếu beat, end ≤ start, crop sai...) bị bỏ qua và in lý do; các entry khác vẫn chạy.

## 3. Thứ tự ưu tiên trên một beat

1. Clip có `beat` ghi rõ.
2. Custom image (`review/custom/custom_images.json`, khoá tay hoặc tự đặt).
3. Panel lock (`review/locks.json`).
4. Panel do matcher chọn.

Clip **không xoá** lớp bên dưới: panel/custom image vẫn nằm trên shot làm **fallback**. Entry chỉ
có `desc` không bao giờ chiếm beat đã có panel lock, custom image hay clip ghi rõ.

## 4. Quy tắc phát

- Một clip phủ nhiều shot của cùng beat: phát **liên tục** (in-point của shot sau = in-point trước
  + thời lượng shot trước), các cú cắt không lộ.
- Nhiều clip cùng một beat: phát theo thứ tự trong manifest, chia nhau thời lượng beat; nếu clip
  nhiều hơn shot thì shot dài nhất bị chia đôi (mỗi nửa ≥ 0,4s; không đủ thì clip thừa bị bỏ, có log).
  Đây là cách đạt nhịp "đổi hình ~2s" kiểu GiyeSears trên một beat dài.
- **Clip ngắn hơn shot: giữ frame cuối** đến hết shot (cả các shot sau của cùng beat khi clip
  đã hết, không phát quá `end`). `shots.json` ghi `frozen_tail_seconds`.
  Clip dài hơn shot thì bị cắt ở cuối shot.
- Fragment key (`3:1`) ghi đè scene key (`3`) trên cùng scene.
- Loop-close (SEAMLESS_LOOP): nếu shot mở đầu là clip thì shot cuối phát lại đầu clip đó; clip ở
  outro nhường phần đuôi cho echo, giống panel outro.

## 5. Render và fallback

- Cắt chính xác `-ss` + `-t`, bỏ audio, `fps=30`, contain + nền blur giống
  `_prepare_panel_frame` (phóng tối đa `CLIP_MAX_UPSCALE`, mặc định 2×). Crop `cx` cần phóng quá
  ngưỡng đó (ví dụ nguồn 720p) thì bỏ crop và dùng contain cả khung. Đặt `CLIP_MAX_UPSCALE=3`
  để nguồn 720p vẫn lấp khung.
- Đầu ra giống hệt shot panel: h264 High@4.0, yuv420p, 1080×1920, 30 fps, đúng `round(dur×30)`
  frame, không audio; SPS trùng bit với shot panel nên `_concat -c copy` an toàn. Mỗi shot được
  ffprobe kiểm lại hợp đồng này sau khi render.
- **Lỗi bất kỳ** (thiếu file, in-point quá cuối clip, ffmpeg lỗi, sai hợp đồng) thì render panel
  như thường. `shots.json` → `clip.fallback_reason`; log cuối ghi `clips: N/M clip shot(s) rendered`.
- `panel_sheet.jpg` hiện frame tại in-point của clip, nhãn `CLIP`.

## 6. CLI tải clip: ghi chú

- `fetch` dùng `yt-dlp --js-runtimes node`, chọn video ≤1080p + audio, ghép mp4, rồi **luôn
  transcode** sang h264 yuv420p 30 fps (cạnh ngắn ≤1080, giữ AAC để xem thử). Cache theo video id:
  chạy lại không tải lại. `YTDLP_BIN` đổi đường dẫn yt-dlp.
- Contact sheet vẽ nhãn thời gian bằng PIL (ffmpeg trên Mac thiếu `drawtext`), tối đa 120 ô (tự
  giãn bước).
- `<id>.json` cạnh clip lưu title/channel/source_url; `add` tự điền `source_url` từ file này.

## 7. Lệnh `moment`: tìm theo khoảnh khắc

- Gửi 3 cách diễn đạt (mô tả, "+ scene clip", "+ animated"; thêm `--query` nếu muốn), gộp mọi video
  tìm được theo video ID, đọc metadata của tối đa `--limit` (mặc định 8) video **mà không tải**.
- Mỗi video có tối đa 3 cửa sổ, mỗi cửa sổ ghi lý do:
  - chapter có tên khớp mô tả;
  - câu phụ đề khớp mô tả (phải chứa từ của mô tả mà tiêu đề KHÔNG có, thường là hành động như
    "fights", "dies", "save"; tên nhân vật có mặt khắp video nên không đủ);
  - đỉnh "Most replayed" (bỏ vài giây đầu: video nào cũng cao ở đó).
- Xếp video theo: độ khớp tiêu đề/mô tả (45%), số cách diễn đạt cùng tìm ra (25%), có tín hiệu thời
  gian (15%), độ dài giống clip cảnh 30s–10 phút (10%), ≥720p (5%). Đây là thứ tự cho Master chọn,
  không loại video nào. Video dọc được gắn nhãn `vertical`.
- Phụ đề hay bị YouTube giới hạn (HTTP 429): khi đó video vẫn có trong danh sách, chỉ mất tín hiệu
  phụ đề.

## 8. Chưa làm (ngoài phạm vi MVP)

Tự khớp clip với beat, gắn nhãn VLM, thư viện clip dùng chung, UI review cho clip.
