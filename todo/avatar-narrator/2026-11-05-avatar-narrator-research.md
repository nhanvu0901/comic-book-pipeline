# Nhân vật dẫn chuyện (avatar narrator) cho Shorts Grimframe — nghiên cứu 2026-11-05

**Ý tưởng của Master:** vẫn kể truyện tranh như cũ, nhưng thêm một nhân vật/avatar có chuyển động, đứng phía dưới khung hình như đang kể chuyện, với nhiều kiểu hoạt hình khác nhau. Mục tiêu là bớt cảm giác "rất AI" và khác hẳn đối thủ.

**Trạng thái:** mới nghiên cứu, chưa code.
Nhãn: [đo] = tự đo/xem · [doc] = tài liệu chính thức · [2nd] = nguồn thứ cấp · [suy] = suy luận.

## 1. Kết luận

1. **Đối thủ đều mặc cùng "đồng phục".** 10/10 video mẫu của 5 kênh đều là panel phủ kín 9:16, chữ chạy 1–3 từ ở giữa khung, giọng AI và nhạc. **Không kênh nào có người dẫn, mặt người, mascot hay nhân vật có biểu cảm** [đo]. Một nhân vật dẫn chuyện vì thế là khác biệt thị giác thật.
2. **Có tăng retention không: chưa biết.** Không có số liệu độc lập nào cho Shorts. Các con số "tăng 300–400%" đều đến từ trang bán công cụ. Bằng chứng gián tiếp chỉ ủng hộ nhẹ: nghiên cứu edX (Guo 2014, 6,9 triệu lượt xem) cho thấy xen mặt người dẫn vào nội dung hấp dẫn hơn chỉ có slide, và nên chèn đúng lúc thay vì phủ suốt video. → Phải test, không nên tin trước.
3. **Nên dùng mascot 2D vẽ tay, không dùng mặt người AI** (HeyGen/D-ID). Mặt người AI dễ gây cảm giác uncanny, tức đúng cái "AI feel" Master muốn bỏ. Các model AI talking-head tốt (SadTalker, MuseTalk, Hallo, EchoMimic…) đều cần GPU CUDA, mà ta không có. Wav2Lip thì cấm dùng thương mại.
4. **Cách làm khả thi, rẻ:** nhân vật cut-out 2D (bộ sprite PNG) + **Rhubarb Lip Sync** (MIT, chạy CPU, nhận audio + transcript, xuất hình miệng A–H/X) + một máy trạng thái Python lái biểu cảm theo beat. Chỉ cần **một lượt overlay ffmpeg** trên video đã ghép. Không tốn tiền API.
5. **Prototype đã chạy trên Mac** [đo]: overlay một "avatar" 1080×520, miệng mở/đóng theo biên độ âm, lên nền 1080×1920; pipe RGBA thẳng vào ffmpeg mất 1,03 s cho 5 s video, ước khoảng 12 s cho một Short 60 s.
6. **Bẫy bố cục:** UI của Shorts che khoảng 20–35% đáy khung. Đặt dải avatar "25–30% dưới cùng" là đúng vùng bị che. → Đặt nhân vật ở **góc dưới-trái, cao 18–22% khung, nằm trên vùng UI**; panel vẫn phủ phần lớn khung; không chia cố định một dải riêng, vì panel đã nhỏ và chữ trong bong bóng sẽ khó đọc hơn.

## 2. Đối thủ trông thế nào (10 video mẫu, tháng 11/2026)

| Kênh | Bố cục | Chữ | Nhịp cắt | Khác thường |
|---|---|---|---|---|
| ComicCandid | Panel phủ kín, zoom vào bong bóng | 2–3 từ trắng viền đen, giữa khung | ~0,6–0,7 cắt/s | Watermark nhỏ |
| Comic_Escape | Panel phủ kín | Từ đơn, xanh lá nhấn | ~0,3–0,6 cắt/s | Icon "Subscribed" nhúng sẵn |
| ComicsUnlocked | Panel phủ kín, đôi khi letterbox | 1–2 từ vàng/trắng | ~0,4–0,6 cắt/s | — |
| GiyeSears | Nền blur màu + panel nhỏ, **xen clip hoạt hình/phim** | Vàng nhấn từ khoá | **~1,5–2 cắt/s** | Sticker nhân vật truyện (Cyclops) ở góc |
| comiczyt | Panel phủ kín, pan chậm | Vàng viền đen | ~0,12–0,15 cắt/s | Dài 95–172 s |

Không kênh nào có nhân vật dẫn chuyện. GiyeSears là kênh duy nhất làm khác: cắt nhanh gấp 3 nhờ chèn clip hoạt hình, cộng sticker chủ thể. Đó là hướng "thêm kích thích thị giác", không phải "persona".

Frame đã tách nằm ở scratchpad của phiên (`avatar_research/*_sheet.jpg`), là file tạm. Trên Mac không có `final.mp4` của chính mình để so cạnh nhau, nên cần render một video mẫu để so.

## 3. Thiết kế nhân vật (nhân vật gốc, không dùng Marvel/DC)

| Concept | Ưu | Nhược |
|---|---|---|
| **A. Quạ "Rook" đậu trên mép panel** | Hợp tông dark/vui; silhouette rõ ở cỡ nhỏ; mỏ đóng/mở dễ lip-sync; đậu lên panel trông tự nhiên | Quạ là motif quen; cần một đặc điểm riêng (kính một tròng, mũ phóng viên…) |
| **B. Chủ tiệm truyện trùm mũ "The Archivist"** | Cá tính lớn, câu cửa miệng dễ viết | Mặt, tay, thân người khó rig và khó giữ nhất quán; dễ uncanny |
| **C. "Panel-head": đầu là một khung panel/trang truyện sống** | Gắn chặt thương hiệu Grimframe; biểu cảm bằng bong bóng thoại, dấu "!", halftone; rất rẻ để làm hoạt hình | Ít "ấm" nếu không có mắt |
| **D. Đầu lâu "Gutter"** (tên theo khoảng trắng giữa các panel) | Dark-fun, hàm dưới mở là lip-sync tự nhiên, hợp giọng châm biếm | Motif bị dùng nhiều; có rủi ro bị xếp là nội dung quá u ám |

**Đề xuất:** A hoặc C cho MVP. Tránh B.

## 4. Hoạt hình theo nhịp truyện

| Nhịp | Trạng thái | Cách làm |
|---|---|---|
| Luôn chạy | idle: thở (scale 1,00–1,02, chu kỳ 3–4 s), chớp mắt ngẫu nhiên 2–5 s | Không bao giờ đứng yên |
| Đang kể | talk: miệng/mỏ theo viseme Rhubarb (hoặc theo biên độ âm ở bản tối giản) | Lọc bỏ cue ngắn hơn 2–3 frame để miệng không nhấp nháy |
| HOOK | lean-in: tiến sát, phóng to nhẹ | Chỉ biến đổi transform, không cần frame mới |
| Twist / CLIMAX | shock: mắt to, rung, zoom 1,05 trong 300–600 ms | Ưu tiên số 1 |
| Cú mỉa / hài | smirk hoặc laugh | Ưu tiên số 2 |
| LANDING | shrug hoặc smirk | Sau MVP |
| Chỉ vào panel | point | Sau MVP |

Luật chung:
- Mỗi trạng thái giữ ít nhất 600 ms.
- Giữa hai phản ứng cần 1,5–2 s cooldown.
- Chuyển trạng thái bằng nội suy vị trí và scale, không crossfade ảnh (crossfade làm mờ nét vẽ).
- Dùng seed theo project để render lặp lại ra đúng cùng kết quả.
- Đầu vào lấy từ dữ liệu có sẵn: `function` của beat (HOOK/ESCALATION/CLIMAX/LANDING), núm cảm xúc theo chunk của Chatterbox, mốc thời gian của `stages/stage_4/beat_timing.py`.

**MVP:** 4 trạng thái (idle, talk, shock, smirk) + 2 chuyển động (lean-in, trượt vào/ra).

**Xuất hiện có chọn lọc:** chỉ ở hook, twist, kết và vài nhịp ngắn giữa video, không phủ suốt, để panel vẫn là nhân vật chính.

## 5. Kỹ thuật

| Cách | Look | Chạy trên máy mình? | Chi phí | Kết luận |
|---|---|---|---|---|
| PNGTuber (miệng đóng/mở theo biên độ) | Phẳng, vẽ tay | Có (CPU) | $0 | Bản tối giản đầu tiên |
| **Cut-out 2D + Rhubarb viseme + máy trạng thái** | Vẽ tay, miệng khớp lời | Có (CPU) | $0 | **Khuyến nghị** |
| Live2D | Đẹp nhất | Render tự động bằng SDK vướng license (có ngưỡng doanh thu, nguồn mâu thuẫn), cần Chromium headless | Rig $300–1.500 | Để sau |
| Spine | Mượt | Runtime cần license ~$369–439 | Có phí | Không cần cho mascot đơn giản |
| Inochi2D | Mở (BSD-2) | Chưa có đường render headless | — | Rủi ro |
| AI talking-head (SadTalker, MuseTalk, Hallo, EchoMimic, LivePortrait) | Dễ uncanny với nhân vật 2D | Cần GPU CUDA | — | Loại |
| API (HeyGen ~$3–4/phút, Runway Act-Two ~$0,05/s cần video diễn xuất, Hedra, D-ID) | Phụ thuộc vendor | Có | $3–4 mỗi Short, tốn lại mỗi lần render | Chỉ để thử ý tưởng |

**Chỗ gắn vào Stage 5:** `concat(-c copy)` → **overlay avatar (1 lần encode)** → burn caption → mix audio. Nên gộp overlay với bước burn caption vào cùng một filtergraph để chỉ encode một lần. Cờ theo project mặc định tắt; tắt thì output giữ nguyên từng byte.

**Kế hoạch file:**
- `stages/avatar/visemes.py`: gọi Rhubarb với `--dialogFile`, cache JSON, fallback theo RMS.
- `stages/avatar/script.py`: dựng timeline sự kiện từ beat.
- `stages/avatar/state_machine.py`: luật chuyển, cooldown, idle, blink theo seed.
- `stages/avatar/render.py`: ghép sprite theo frame, pipe RGBA ra ffmpeg.
- `stages/avatar/assets/<character>/`: sprite + `manifest.json`.
- Sửa nhỏ trong `stages/stage_5/pipeline.py` và `captions.py` (đẩy caption né nhân vật khi cờ bật).

**Lưu ý kỹ thuật:**
- Rhubarb phải chạy trên **đúng bản narration cuối**, kể cả sau khi đã chỉnh tempo (`POST_ATEMPO`), nếu không miệng sẽ lệch tiếng.
- Pipe rawvideo phải khai `-pix_fmt rgba -s WxH -r 30` trước `-i -`, và dùng `overlay=...:format=auto:shortest=1`.
- Chưa test trên Windows/AMD và chưa thử gộp chung với `subtitles` ASS.

## 6. Làm art nhân vật
- **Thuê artist:** PNGTuber khoảng $50–200, biểu cảm phụ tính thêm; rig Live2D $300–1.500. Hợp đồng phải ghi rõ quyền thương mại, quyền sửa, và nhân vật là bản gốc.
- **Hoặc tự vẽ** bằng Krita (miễn phí).
- **AI** chỉ nên dùng để phác concept, người vẽ lại sạch, vì Master không muốn "AI feel" và AI khó giữ cùng một nhân vật qua 8 biểu cảm.
- **Spec:**
  - Canvas 1080×540, vẽ ở 2×, có layer: thân, đầu, tay trái/phải, mày, mắt mở/nửa/nhắm.
  - 9 hình miệng theo Rhubarb (A–H, X).
  - 6–8 biểu cảm: neutral, lean-in, shocked, laugh, smug, sad, angry, thinking.
  - Viền đậm và bảng màu hạn chế, để đọc được trên điện thoại và không lẫn vào màu panel.

## 7. Đề xuất bước tiếp
1. **Render 1 video mẫu hiện tại của mình** và đặt cạnh frame đối thủ, xem panel có còn đọc được khi chừa chỗ cho nhân vật không.
2. **Chọn concept** (A quạ Rook hoặc C panel-head), rồi vẽ bản placeholder hoặc đặt artist.
3. **MVP 1–2 tuần:**
   - Ngày 1–2: art spec, chạy thử Rhubarb trên audio TTS thật.
   - Ngày 3–4: miệng + blink + overlay.
   - Ngày 5–6: máy trạng thái theo beat.
   - Ngày 7: gắn vào Stage 5 sau cờ.
   - Ngày 8–10: test trên server, A/B.
4. **A/B:**
   - 15–20 cặp chủ đề tương đương, đăng luân phiên.
   - Đo viewed-vs-swiped, average % viewed, subs/view; không dùng view thô.
   - Đặt ngưỡng trước, ví dụ +5 điểm phần trăm.
   - Tách hai thử nghiệm: nhân vật xuất hiện ngay từ khung đầu, và nhân vật vào sau 2–3 s, vì khung đầu làm thay đổi cả hook.

## 8. Chưa kiểm chứng
- Chưa tìm được kênh Shorts truyện tranh nào có avatar kèm số liệu trước/sau.
- Vùng an toàn chính thức của Shorts: các nguồn mâu thuẫn, cần thử trên điện thoại thật.
- Tốc độ Rhubarb thực tế; định dạng chính xác của `beat_timing`; giá trị `MarginV` hiện tại của caption.
- License gốc của Live2D/Spine: chưa đọc được trang gốc.

## Nguồn chính
- **Cục bộ:** `research/competitor_refresh_2026-11-03/` (10 mp4 + `manifest.csv`), `COMPETITOR_CORPUS_FULL_AUDIT_2026-10-01.md`.
- **Lip sync:**
  - Rhubarb Lip Sync: https://github.com/DanielSWolf/rhubarb-lip-sync
  - License Rhubarb: https://raw.githubusercontent.com/DanielSWolf/rhubarb-lip-sync/master/LICENSE.md
- **Engagement và hình thức video:**
  - Guo, Kim, Rubin 2014 (edX): https://www.cs.rochester.edu/hci/pubs/pdfs/edX-MOOC-video-production-and-engagement_LAS-2014.pdf
  - Sludge content (split-screen): https://en.wikipedia.org/wiki/Sludge_content
  - Chính sách inauthentic/reused content: https://support.google.com/youtube/answer/1311392
- **Rig 2D:**
  - Live2D license: https://help.live2d.com/en/?p=2368
  - Spine: https://esotericsoftware.com/spine-purchase
- **Giá và chi phí:**
  - HeyGen API: https://help.heygen.com/en/articles/10060327-heygen-api-pricing-explained
  - Runway: https://www.activepieces.com/blog/runway-api-pricing-per-second-for-every-video-model
  - Giá thuê vẽ PNGTuber: https://www.fiverr.com/ayanostudio/design-custom-pngtuber-character-model-for-streaming
  - Giá rig Live2D: https://shiralive2d.com/live2d/how-much-does-a-live2d-model-cost/
- **License các model lip-sync mã nguồn mở:** https://www.creativeainews.com/articles/open-source-lip-sync-licenses-commercial-use-2026/
- **Vùng an toàn Shorts** (số liệu lệch nhau): https://reap.video/blog/short-form-video-safe-zones
- **Pipeline tham khảo Blender + Rhubarb + FFmpeg:** https://github.com/semanticintent/semantic-foragecast-engine
