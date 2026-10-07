# Handoff: phân tích GiyeSears và khả năng dùng clip MP4 trong comic-book-pipeline

Ngày khảo sát: **2026-10-06**. Trạng thái: **đã khảo sát, chưa sửa pipeline**.

## 1. Kết luận để người tiếp nhận bắt đầu nhanh

- Video tham chiếu [“HOW DOES MOGO RECHARGE ITS RING?”](https://www.youtube.com/shorts/b0RfYBoQkcA) của [@GiyeSears](https://www.youtube.com/@GiyeSears/shorts) **không dùng MP4 thay hoàn toàn comic panel**. Nó ghép hoạt hình, panel comic, ảnh/crop, hiệu ứng chuyển động và caption. Ba mẫu khác xác nhận kênh thay đổi nguồn hình theo chủ đề: comic + hoạt hình, cảnh người đóng MCU, hoặc hoạt hình gần như toàn bộ.
- Công thức lặp của kênh: một câu hỏi/vấn đề cụ thể ở đầu, lời kể khoảng **217–231 từ/phút**, hình minh họa đổi theo từng ý, caption trắng/vàng viền đen theo cụm ngắn, và một thông tin mới hoặc đổi đối tượng ở giữa để giữ chú ý. **Không có bằng chứng rằng 60 fps, tốc độ đọc, hay chỉ riêng MP4 tạo view cao.**
- You.com Web Search/Research là lớp **tìm URL và bằng chứng**, không phải API cấp file MP4. Hai truy vấn live trả 25 URL trang, **0 URL `.mp4` trực tiếp**. Một phép thử tiếp theo đã xác nhận chuỗi **You.com tìm URL clip hoạt hình trên YouTube → yt-dlp tải/ghép thành MP4** hoạt động với một video DC Kids công khai. Contents API tự nó vẫn chỉ trả HTML/Markdown. Muốn dựng bằng clip cần lớp asset video riêng, kiểm tra quyền sử dụng, chọn mốc vào/ra, đối chiếu hình với câu thoại, rồi render.
- Hướng triển khai đáng thử: **hybrid panel + clip video**, giữ panel đúng issue làm bằng chứng, dùng clip khi có cảnh đúng nhân vật/sự kiện và quyền phù hợp. Thiết kế chung theo loại asset và visual beat; không hard-code một comic hoặc nhân vật.

## 2. Phạm vi và tệp bằng chứng

Thư mục artifact: `/Volumes/HIKSEMI/MMO/facebook/video/youtube_analysis/`.

| ID | Video | Thời lượng thực qua ffprobe | Groq SRT | Tệp |
|---|---|---:|---:|---|
| `b0RfYBoQkcA` | [Mogo recharge](https://www.youtube.com/shorts/b0RfYBoQkcA) | 61,9s | 236 từ, ~229 wpm | `.mp4`, `.mp3`, `.srt`, `.info.json` |
| `AkItCdi-4iE` | [Entities of the Lanterns](https://www.youtube.com/shorts/AkItCdi-4iE) | 76,7s | 294 từ, ~230 wpm | tương tự |
| `CPZGAU6ppwk` | [Infinity Stones turn into people?](https://www.youtube.com/shorts/CPZGAU6ppwk) | 76,5s | 276 từ, ~217 wpm | tương tự |
| `0ACa4ItXhEE` | [Green Lanterns vs Manhunters](https://www.youtube.com/shorts/0ACa4ItXhEE) | 60,7s | 234 từ, ~231 wpm | tương tự |

File gốc của mỗi video là `<ID>.mp4`; file âm thanh là `<ID>.mp3`; transcript timestamped là `<ID>.srt`; metadata yt-dlp là `<ID>.info.json`. Groq transcriber sẵn tại `/Volumes/HIKSEMI/MMO/facebook/groq-transcriber/transcribe.py` đã chạy thành công, không cần sửa script. SRT là **timestamp theo câu/cụm**, không phải timestamp chính xác từng từ của caption trên màn hình. Tên riêng trong ASR phải kiểm tra lại trước khi dùng làm nguồn canon.

Danh sách 25 Shorts gần đây: `GiyeSears_recent_shorts.csv` cùng thư mục. Kết quả `yt-dlp --flat-playlist` có ID, title, view_count, URL; **duration và upload_date rỗng**, nên phải đọc metadata từng video nếu cần so sánh theo tuổi bài đăng.

Contact sheets để xem nhanh hình:

- Mogo, mỗi 2 giây: `stills/contact_2s.jpg`
- Entities, mỗi 3 giây: `stills/AkItCdi-4iE_contact_3s.jpg`
- Infinity Stones, mỗi 3 giây: `stills/CPZGAU6ppwk_contact_3s.jpg`
- Manhunters, mỗi 3 giây: `stills/0ACa4ItXhEE_contact_3s.jpg`

Kiểm tra cuối: cả 4 bộ MP4/MP3/SRT/metadata đều tồn tại, không rỗng; ffprobe đọc được video; cả 25 hàng CSV có view count. Không sửa file code trong repo ở lần khảo sát này.

## 3. Video Mogo: cấu trúc narration

Metadata yt-dlp tại lúc khảo sát: kênh **GiyeSears**, handle `@GiyeSears`, channel ID `UCyIZgUiDFm0-AV5hFmrx6Pw`; đăng **2026-09-20**; khoảng **1.324.132 view** và **33.558 like**. Video 1080×1920, 60 fps. Số view/like thay đổi theo thời gian.

| Mốc | Chức năng trong câu chuyện | Hình quan sát được |
|---|---|---|
| 0–3s | Hook dạng câu hỏi nghịch lý: một hành tinh Green Lantern sạc nhẫn ra sao? | Hành tinh/nhẫn và chữ lớn. |
| 3–10s | Nêu trở ngại: kích thước Mogo khiến cách đeo nhẫn thông thường không dùng được. | Hình hành tinh, Green Lantern, góc cận. |
| 10–18s | Payoff đầu: nhẫn và power battery nằm trong lõi. | Panel comic được đặt trên nền động/blur; hình nhẫn/lõi. |
| 18–30s | Lợi thế: nhẫn gần như luôn được cấp năng lượng. | Nhiều cảnh hoạt hình năng lượng xanh, thay hình theo mệnh đề. |
| 28–32s | Mở vòng tò mò thứ hai bằng lời báo có chi tiết quan trọng hơn. | Hình chuyển sang trạng thái năng lượng/cơ thể Mogo. |
| 31–42s | Stakes: nguồn năng lượng còn liên quan đến ý thức và hoạt động của Mogo. | Hình năng lượng → sinh hoạt/cấu trúc hành tinh. |
| 42–54s | Ví dụ xung đột Parallax; lời kể đưa cơ chế thành một tình huống cụ thể. | Hình phản diện, battery, Mogo mất năng lượng. |
| 54–62s | Chốt trở về câu hỏi ban đầu: pin trong lõi giúp hành tinh hoạt động như Green Lantern. | Trở lại hành tinh/biểu tượng Lantern. |

Điểm học được: **giải đáp một phần sớm, rồi tăng hệ quả**. Tránh sao chép các câu nối rỗng như “there is another detail”; một câu nối tốt nên tiết lộ thông tin mới. Lời thoại Mogo dùng nối nhân quả rõ nhưng lặp “That’s why”; bản của mình có thể ít lặp hơn. Không có CTA dài hoặc outro card tách người xem khỏi câu chuyện.

**Cảnh báo về độ chính xác nguồn:** transcript nói Parallax “turned off his battery”. [DC mô tả Parallax phá Central Power Battery trên Oa](https://www.dc.com/blog/2023/03/07/emerald-allies-hal-jordan-and-john-stewart-s-close-friendship); [DC mô tả Mogo ngủ yên khi Highfather lấy chiếc nhẫn trong một sự kiện khác](https://www.dc.com/blog/2021/12/09/ask-the-question-does-mogo-ever-create-constructs). Cần kiểm tra issue/continuity trước khi dùng câu chuyện đó như một sự kiện chính xác; không suy ra clip sai tuyệt đối nếu nó dựa vào continuity khác.

## 4. Ba mẫu khác: điều gì lặp lại, điều gì thay đổi

| Video | Hook/narrative | Nguồn hình quan sát được | Metadata tại lúc khảo sát |
|---|---|---|---|
| Entities | Hỏi “những thực thể nào cấp sức mạnh cho các Lantern?”, rồi chuyển từng Corps; mỗi thực thể là một lần reset chú ý. Kết bằng chi tiết Necron/Anti-Monitor. | Comic panel + hoạt hình, đổi màu theo Corps. | Đăng 2026-09-12; ~1.679.316 view, 77s. |
| Infinity Stones | Hỏi một giả thuyết MCU, rồi ghép từng Stone với nhân vật; lời thoại dùng “would”, cuối có độ mở về Soul Stone. | Phần lớn là cảnh người đóng MCU. | Đăng 2026-08-15; ~73.761 view, 76s. |
| Manhunters | Mở bằng bối cảnh Hal trở về Earth, kể arc Hal/Guy → Manhunters → phối hợp → kết quả. | Gần như toàn cảnh hoạt hình Green Lantern; crop dọc và nền fill. | Đăng 2026-10-06; ~14.532 view, 61s. **Mới đăng cùng ngày khảo sát**, không được dùng view thấp để kết luận format kém. |

Cả bốn video đều có voiceover tiếng Anh, caption ngắn theo nhịp nói với từ nhấn màu vàng trên nền chữ trắng/viền đen, khung dọc và thay hình thường xuyên. Nhiều frame ngang được crop hoặc đặt trên nền phóng mờ. **Nguồn hình có thể là cảnh video hoặc panel**, không có một quy tắc “MP4 cho mọi beat”.

Trong 25 Shorts gần đây, 16 title thuộc cụm Green Lantern/Mogo/Sinestro; 4 thuộc Spider-Man; 3 nêu Mogo. Median view toàn mẫu là khoảng **68.000**; mean khoảng **281.920** vì vài hit lớn kéo lên. Năm video view cao nhất trong mẫu đều thuộc cụm Lantern, nhưng tuổi đăng và mức phân phối khác nhau. Đây là **giả thuyết để thử cluster chủ đề**, không phải bằng chứng nhân quả. Tốc độ của mẫu nhiều view và mẫu mới ít view gần bằng nhau; tăng tốc narration đơn thuần không được dữ liệu này ủng hộ.

ffmpeg scene detector trên Mogo trả 78/43/27 thay đổi ở ngưỡng 0,25/0,35/0,45. Hiệu ứng flash và chuyển động trong clip tạo nhiều điểm giả; **không dùng các số này làm số cut thật**. Contact sheet chỉ chứng minh nhiều hình khác nhau xuất hiện trong suốt clip; muốn có median shot length chính xác cần annotate thủ công hoặc thuật toán tách cảnh tốt hơn. Chưa đánh giá timbre giọng, nhạc hay sound effect bằng nghe trực tiếp.

## 5. Kết quả kiểm tra You.com và nguồn MP4

Thử live Web Search API bằng API key local mà không ghi key vào output:

1. `official Marvel video trailer mp4 filetype:mp4`: 10 kết quả web; **0 direct `.mp4`**. Kết quả gồm trang Marvel, YouTube, Facebook, Archive.org, v.v. Từ “.mp4” trong title không đồng nghĩa URL file MP4.
2. `Mogo Green Lantern animated series clips video mp4`: 15 kết quả web; **0 direct `.mp4`**. Kết quả là trang Fandom, IMDb, streaming, bài viết và thông tin khác.

[You.com Web Search API](https://you.com/docs/guides/search) trả web/news result với URL, title, snippets, thumbnail; [Contents API](https://you.com/docs/guides/contents) trích HTML/Markdown. Không có output chuẩn dạng bytes MP4 hay `video_files[].link`. You.com có thể phát hiện một trang/video URL; sau đó cần bộ lọc domain, xác minh URL thực, định dạng, quyền và chất lượng. Không nên hứa rằng You.com *không bao giờ* index một URL `.mp4`; kết luận là **API này không cung cấp MP4 như một asset service đáng tin cậy**.

### 5a. Phép thử tải cartoon MP4 từ kết quả You.com

Sau câu hỏi bổ sung của Master, đã thử chuỗi đầy đủ thay vì chỉ kiểm tra schema. Web Search API với truy vấn `site:youtube.com Green Lantern The Animated Series Manhunters official clip` và `include_domains: ["youtube.com"]` trả URL [“Green Lantern: The Animated Series | Fighting the Anti-Monitor! | @dckids”](https://www.youtube.com/watch?v=gKiT1ekWIAA). `yt-dlp` xác nhận video công khai thuộc channel **DC Kids** và tải được file thử ở:

`/Volumes/HIKSEMI/MMO/facebook/video/youtube_analysis/youcom_cartoon_test/gKiT1ekWIAA.mp4`

Lệnh tải đã chạy thành công:

```bash
yt-dlp --no-playlist --js-runtimes node \
  -f 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]' \
  --merge-output-format mp4 \
  -o '/Volumes/HIKSEMI/MMO/facebook/video/youtube_analysis/youcom_cartoon_test/gKiT1ekWIAA.%(ext)s' \
  'https://www.youtube.com/watch?v=gKiT1ekWIAA'
```

`ffprobe` xác nhận file **228,58 giây, 640×360, 9.939.322 byte, video AV1 25 fps + audio AAC**. [Contact sheet cartoon](</Volumes/HIKSEMI/MMO/facebook/video/youtube_analysis/youcom_cartoon_test/gKiT1ekWIAA_contact.jpg>) cho thấy đây thực sự là cảnh hoạt hình Green Lantern. **MP4 là container, không đảm bảo codec H.264**: trước khi Stage 5 dùng clip này cần kiểm tra codec và transcode/normalize nếu renderer yêu cầu H.264, 30 fps, 1080×1920. Bản tải 360p chỉ để chứng minh chuỗi hoạt động, chưa đạt chất lượng khung dọc xuất bản.

Không phải mọi kết quả You.com đều tải được. Các kết quả kiểm tra khác gồm URL private/không khả dụng; một bản Mogo do tài khoản không chính thức đăng có metadata công khai và format MP4 nhưng khi tải bị HTTP 403. Bỏ `--js-runtimes node` cũng làm một số video không lấy đủ metadata/challenge. **You.com không xác minh availability, quyền hoặc tải MP4 hộ ta.** Clip DC Kids này chỉ là artifact nghiên cứu kỹ thuật; chưa có quyền dùng lại trong video xuất bản.

[Pexels Video API](https://www.pexels.com/api/documentation/) là ví dụ API thực sự trả `video_files[].link` và hỗ trợ tìm video portrait. Dùng được cho stock chung (không gian, thành phố, hiệu ứng nền), nhưng không giải quyết clip DC/Marvel đúng sự kiện. Nguồn khác là thư viện MP4 đã được cấp quyền hoặc do người dùng cung cấp. You.com vẫn hữu ích ở tầng nghiên cứu lore và tìm trang ứng viên.

Quyền dùng lại là kiểm tra riêng với tính năng tải file. [YouTube mô tả chính sách reused content](https://support.google.com/youtube/answer/1311392) dựa trên giá trị bình luận/chỉnh sửa gốc; [hướng dẫn monetization](https://support.google.com/youtube/answer/2490020) yêu cầu quyền phù hợp với hình/âm thanh dùng thương mại. Việc tìm được clip qua You.com không cấp quyền sử dụng clip đó.

## 6. Repo hiện có gì để tận dụng

- `stages/stage_3/beat_split.py` và narration `visual_beats`: câu chuyện đã được chia thành khoảnh khắc có thể minh họa. Giữ interface này làm đơn vị chọn panel/video.
- `stages/stage_5/schema.py`: `Shot` hiện có `source_image` và `custom_image`; chưa có `source_video`, mốc `in/out`, hay nguồn quyền.
- `stages/stage_5/shots.py:render_shot`: lấy ảnh panel/custom image, chuẩn hóa vào khung rồi làm Ken Burns ra MP4. Muốn nhập clip thật phải thêm renderer video riêng hoặc nhánh media type, không truyền MP4 vào `custom_image`.
- `stages/stage_5/pipeline.py:assemble_project`: nhận narration, `audio.wav`, `word_timestamps.json`, caption chunks và preprocessed pages; render shots rồi ghép `final.mp4`. Short mặc định 1080×1920, 30 fps. Chênh lệch với export 60 fps của đối chứng **không chứng minh cần đổi fps**.
- `stages/stage_5/shots.py`: `MAX_SHOT_SECONDS` mặc định `9999`, tức split shot dài bị tắt. Có thể thử nhịp ngắn hơn trong pilot; ưu tiên đổi sang hình/clip thật sự mới, không chỉ cắt nhiều lần trên cùng một panel.
- Stage 5 hiện không bake caption vào final MP4; CapCut ở `/Volumes/HIKSEMI/MMO/facebook/generate_capcut_project.py` có luồng caption Groq/CapCut riêng. So sánh format phải dùng bản **đã hoàn thiện caption**, không so với file Stage 5 trần.
- `stages/research_scout/youcom.py` gọi Search/Research để lấy bằng chứng văn bản; `stages/youcom_scout.py` có whitelist nguồn comic/Reddit. Luồng scout này chưa phải media asset discovery.

## 7. Đề xuất pilot và thiết kế nếu triển khai

**Pilot biên tập trước, chưa cần đổi pipeline chung:** chọn một câu hỏi Q&A có cơ chế lạ + hậu quả cụ thể. Dùng panel làm bằng chứng canon, vài clip đã rõ quyền/nguồn cho các beat có hành động phù hợp. Duyệt storyboard gồm câu thoại, frame đầu/giữa/cuối của clip, issue/panel và source URL trước khi render. So với một video panel-only cùng cluster bằng retention 3 giây, average view duration, completion, rewatch, thời gian sản xuất và claim. Không quy view thành hiệu quả của media type khi ngày đăng/chủ đề khác nhau.

**Nếu pilot đáng làm tiếp, cơ chế code nên tổng quát:**

1. Asset manifest ở cấp project, mỗi media có `kind` (`panel`/`video`), path/URL, nguồn, thông tin quyền, thời lượng và thumbnail.
2. Mapping từ mỗi `visual_beat` đến media và đoạn `start/end` của video. Chọn theo nhân vật, hành động, bối cảnh, continuity và độ khớp câu thoại; không chọn chỉ vì màu/subject gần giống.
3. Duyệt contact sheet/storyboard trước render; đánh dấu shot sai sự kiện và asset không được phép dùng.
4. Stage 5 thêm video renderer: probe codec/duration, cắt đúng mốc, mute âm gốc, xử lý tỷ lệ 9:16 bằng crop theo chủ thể hoặc contain+blur, chuẩn hóa fps/resolution, giữ tổng shot duration bằng audio timeline.
5. Fallback về panel khi không có clip đạt gate. Cơ chế áp dụng cho mọi comic/nhân vật, không hard-code Mogo, Lantern hay số trang.

Các ngưỡng shot length/tỷ lệ clip nên là **tham số pilot có đo lường**, không được suy ra từ một Short rồi đóng cứng vào shared code. Không thay đổi Stage 1/3 nhằm chạy theo lỗi lore của video tham chiếu; giữ gate nguồn và canon hiện có.

## 8. Việc còn mở

- Chưa xác định nguồn gốc và quyền của từng cảnh hoạt hình/MCU trong GiyeSears; contact sheet chỉ cho thấy loại hình ảnh.
- Chưa nghe và đo trực tiếp nhạc nền, SFX hoặc âm sắc voice; phân tích narration dựa trên Groq SRT và cấu trúc hình.
- Chưa triển khai tính năng video asset trong repo, chưa render pilot, chưa có retention A/B.
- Cần source verification cho câu chuyện Mogo/Parallax nếu muốn dùng làm đề tài của kênh mình.

## 9. Nguồn và lệnh tái kiểm tra

Nguồn ngoài: [You.com Search](https://you.com/docs/guides/search), [You.com Contents](https://you.com/docs/guides/contents), [Pexels Video API](https://www.pexels.com/api/documentation/), [YouTube reused content](https://support.google.com/youtube/answer/1311392), [YouTube monetization rights](https://support.google.com/youtube/answer/2490020), [DC về Parallax](https://www.dc.com/blog/2023/03/07/emerald-allies-hal-jordan-and-john-stewart-s-close-friendship), [DC về Mogo](https://www.dc.com/blog/2021/12/09/ask-the-question-does-mogo-ever-create-constructs).

Ví dụ kiểm tra artifact:

```bash
ffprobe -v error -show_entries format=duration:stream=codec_type,width,height,avg_frame_rate -of json "/Volumes/HIKSEMI/MMO/facebook/video/youtube_analysis/b0RfYBoQkcA.mp4"
ls -lh "/Volumes/HIKSEMI/MMO/facebook/video/youtube_analysis/"{b0RfYBoQkcA,AkItCdi-4iE,CPZGAU6ppwk,0ACa4ItXhEE}.{mp4,mp3,srt,info.json}
```
