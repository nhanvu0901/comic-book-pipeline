# Lấy clip mp4 ngắn (2–4 s) của nhân vật để chèn vào shot quan trọng — nghiên cứu 2026-10-04

Mục tiêu: giữ panel comic là hình chính, chỉ ở hook, cảnh hành động và twist thì chèn **một clip có sẵn** (không tạo bằng AI) của đúng nhân vật. Tối đa 2–3 clip mỗi Short. Chưa code.
Hai nhánh nghiên cứu: nguồn hợp lệ + giấy phép, và cách lấy/khớp tự động.
Nhãn: [doc] = tài liệu chính thức · [quan sát] = tự đo trên trang công khai · [suy] = suy luận.

## 1. Kết luận nhanh

| Nguồn | Nhân vật có | Rủi ro pháp lý | Rủi ro Content ID | Tự động hoá | Dùng? |
|---|---|---|---|---|---|
| **A. Trailer CC-BY trên kênh YouTube chính thức của WB Games** (@mortalkombat 24/24 video CC, @MultiVersus, LEGO Batman), lấy qua bản mirror Wikimedia Commons | Batman, Superman, Joker, Wonder Woman, Peacemaker (DC); Omni-Man (Invincible), Homelander (The Boys), Spawn (Image) | Thấp–TB: DC thấp vì WB là chủ IP; Omni-Man/Homelander/Spawn trung bình | Thấp–TB | Cao (Commons API có metadata giấy phép) | **Nguồn chính** cho truyện DC, Image, The Boys |
| **B. Tự quay gameplay** (Marvel Rivals, Spider-Man 2, Wolverine, Arkham), chỉ gameplay thuần, không cutscene, tắt nhạc | Hulk, Spider-Man, Wolverine, Batman… | TB–Cao: ToS các hãng ghi "non-commercial", thực tế được dung thứ | Thấp (gameplay thuần); cao (cutscene, nhạc) | TB (cần người chơi + OBS) | **Dự phòng chính cho Marvel** |
| C. Trailer CC-BY của Capcom France (Marvel vs. Capcom, đồ hoạ 2D) | Hulk, Spider-Man… kiểu arcade | TB–Cao: Capcom không có quyền cấp phép IP Marvel | Thấp–TB | Cao | Chỉ khi chấp nhận rủi ro |
| D. Superman Fleischer 1941–43 (17 phim, Internet Archive 1080p) | Chỉ Superman | TB: phim được cho là public domain ở Mỹ (không gia hạn bản quyền), nhưng nhân vật/nhãn hiệu vẫn được bảo hộ | TB: bản phục chế của WB có thể bị claim | Cao | Chỉ cho truyện Superman |
| E. Quay đồ chơi/LEGO | Mọi nhân vật | TB | Thấp | Thấp | Dự phòng cho beat tĩnh |
| F. Remix "Cut" của YouTube | Tuỳ kênh | Thấp | Thấp–TB | **Không** (chỉ làm được trong app điện thoại) | Thao tác tay |
| G. Kho stock/editorial (Getty, Shutterstock, Adobe) | Không có cảnh nhân vật hành động | Cao (editorial cấm thương mại) | — | — | Không |
| H. Mua clip từ studio (Disney/WB) | Có | Thấp | Thấp | Thấp | Quá đắt/chậm, không có bảng giá công khai |
| I. Clip phim/fan gắn CC hoặc PD bởi người không sở hữu IP (đầy trên YouTube, Internet Archive) | — | **Rất cao** | Cao | — | **Tránh** |

**Câu trả lời ngắn:**
- **Truyện DC, Invincible, The Boys:** lấy từ trailer CC-BY chính thức của WB Games, qua Wikimedia Commons.
- **Truyện Marvel:** không có nguồn sạch. Phải tự quay gameplay (Marvel Rivals cho Hulk/Spider-Man), hoặc giữ panel có chuyển động + SFX.

## 2. Luật YouTube áp lên mọi nguồn
- "Bất kỳ lượng nội dung có bản quyền nào… **dù chỉ vài giây**" vẫn có thể gây vấn đề. Ghi credit hay câu "no infringement intended" không phải lá chắn. [doc: support.google.com/youtube/answer/2797449]
- **Short trên 1 phút có claim Content ID đang hoạt động thì bị chặn**, bất kể chính sách của bên claim. → Short có clip của bên thứ ba nên ≤60 giây, hoặc phải kiểm kỹ. [doc: /answer/6013276, /answer/15424877]
- 3 cảnh cáo bản quyền trong 90 ngày thì kênh bị xoá, kéo theo cả kênh liên kết. [doc: /answer/2814000]
- Chính sách "reused content" áp dụng **kể cả khi có phép của người gốc**. Clip ngắn + lời kể gốc + panel thì nằm trong vùng được phép, miễn clip chiếm tỷ lệ nhỏ. [doc: /answer/1311392]
- YouTube không cho gắn CC lên video đang có claim Content ID. Vì vậy video chính thức mang cờ CC thường "sạch". [doc: /answer/2797468]

## 3. Chi tiết từng nguồn
### 3.1 Trailer CC-BY chính thức
- Tỷ lệ video gắn CC-BY trên kênh chính thức, đếm ngày 2026-10-04 [quan sát]:

  | Kênh | Video CC-BY |
  |---|---|
  | @mortalkombat | 24/24 |
  | @CapcomFrance | 24/24 |
  | @MultiVersus | 5/20 |
  | @wbgames | 2/30 |
  | @DCofficial | 1/24 |
  | Marvel, Marvel Rivals, PlayStation, Insomniac, Capcom USA, Marvel Snap, MCoC, 2K | 0 |

- Wikimedia Commons đã mirror các trailer Mortal Kombat 1 dưới CC BY 3.0, có tình nguyện viên kiểm giấy phép. Có template "Free depiction (Mortal Kombat)": phát hành tự do "by or with permission from NetherRealm Studios, Warner Bros. Games". Ví dụ có các trailer gameplay Homelander, Omni-Man, Peacemaker ở 4K. [doc]
- Giấy phép CC BY không thu hồi được với bản đã phát hành. Cần lưu bằng chứng: URL, ngày tải, ảnh chụp dòng License.
- **Hạn chế:**
  - CC chỉ cấp phần quyền mà người cấp phép thật sự có. WB cấp trên nhân vật DC của chính WB thì mạnh. Omni-Man, Homelander, Spawn và Marvel qua Capcom thì chuỗi quyền yếu.
  - Tải trực tiếp từ YouTube vi phạm ToS của YouTube. → Tải bản mirror trên Commons, hoặc tải tay một lần cho thư viện nhỏ.
  - Luôn tắt âm thanh gốc: nhạc và VO của bên thứ ba có thể bị claim.
  - Ghi credit theo mẫu YouTube: tên tác phẩm, tác giả, URL, "licensed under CC BY".

### 3.2 Gameplay tự quay
- Không hãng nào cấp phép rõ cho nhân vật Marvel/DC:
  - PlayStation ToS: "personal, non-commercial".
  - Warner ToU: cũng "non-commercial".
  - NetEase (Marvel Rivals): cấm "host or stream" trừ khi được phép; có "Partner Hub" cho creator, điều khoản không công khai.
  - Epic Fan Content Policy chỉ cho Epic IP, **không phủ skin Marvel/DC**. [doc]
- Thực tế video gameplay được dung thứ rộng rãi [suy]. Chỉ dùng gameplay thuần, không cutscene, không nhạc.
- Nên xin phép qua chương trình creator chính thức: Marvel Rivals Partner Hub, Marvel Snap Creators, Capcom Creators.
- Năng suất ước ~20–40 clip dùng được mỗi giờ chơi [suy].

### 3.3 Public domain
- **Superman Fleischer/Famous Studios 1941–43 (17 phim):** được cho là public domain ở Mỹ vì không gia hạn bản quyền. Có trên Internet Archive (`fleischer-superman`, 17 file MP4 1080p). Chưa kiểm ở mức hồ sơ của Copyright Office.
  - Nhân vật, chữ "S" và tên Superman vẫn được bảo hộ (Action Comics #1 vào public domain năm 2034).
  - Tránh bản phục chế Blu-ray 2023 của WB, bản tô màu, bản AI-upscale.
  - Public domain chỉ ở Mỹ.
  - Vài phim thời chiến có hình ảnh biếm hoạ dân tộc.
- Không có public domain cho Hulk, Spider-Man, Batman. Serial Captain America 1944 đã gia hạn năm 1971, không phải public domain.

### 3.4 Những thứ không dùng được
- **Bộ lọc CC trên YouTube và Internet Archive đầy clip "license washing".** Ví dụ "Avengers Endgame Hulk Smash Scene" gắn CC, cảnh game Spider-Man gắn CC, phim Marvel Animation gắn CC0. Người không sở hữu nhân vật thì không cấp phép được: CC FAQ, 17 U.S.C. §103.
- **Fan film, cosplay, fan animation:** người làm không sở hữu nhân vật; xin phép họ không đủ.
- **Stock editorial:** cấm dùng thương mại, và cũng không có cảnh nhân vật hành động.

## 4. Lấy và khớp clip tự động (không dùng embedding)
- **Dựng sẵn thư viện clip cho từng nhân vật,** không tìm theo từng Short.
  - Quota YouTube `search.list` chỉ 100 lần/ngày, áp dụng từ 01/06/2026.
  - Mỗi clip được duyệt tay một lần.
  - 100 clip/nhân vật ≈ 0,3–0,5 GB: master 3–6 s không tiếng, ≤1080p, cộng proxy 360p.
  - Mỗi clip một dòng JSON gồm: nhân vật, hành động, nguồn, giấy phép, credit, khung crop, kết quả kiểm Content ID, cờ cấm.
- **Lấy về:**
  - Wikimedia Commons API: có `extmetadata` license, tác giả, nguồn.
  - Internet Archive (`internetarchive` lib): trường license do người upload điền, không tin được → **allow-list ID do người duyệt**, không lọc tự động.
  - Không tải YouTube hàng loạt bằng yt-dlp (vi phạm ToS, hay gặp lỗi PO token). Chỉ dùng YouTube Data API để tìm manh mối và kiểm cờ `status.license`.
- **Cắt:**
  - PySceneDetect (`AdaptiveDetector` cho game, `ContentDetector` cho cartoon) để tìm ranh giới cảnh.
  - Chọn cửa sổ 2–4 s có chuyển động lớn (frame-diff/optical flow), trừ điểm nếu có chữ, HUD, logo.
  - Cắt chính xác bằng `ffmpeg -ss` đặt trước `-i` và encode lại; luôn `-an`.
- **Gắn nhãn:**
  - Nhân vật: khoá 1 lấy theo metadata nguồn, khoá 2 do VLM xác nhận (hỏi theo danh sách đóng).
  - Hành động: enum ~15 loại (strike, smash, leap, fly, swing…).
  - Chi phí ~$0,0005/clip với Gemini 3.1 Flash-Lite. VLM hay nhầm biến thể trang phục, nên cần **golden set ~50 frame** gắn nhãn tay trước khi tin.
- **Khớp với beat:**
  - Cổng cứng: nhân vật của clip phải nằm trong nhân vật của beat.
  - Điểm: trùng hành động (động từ trong `caption_text` → enum) + `token_jaccard` có sẵn + mức chuyển động − phạt dùng gần đây.
  - Không qua ngưỡng thì **giữ panel**.
  - Khe chèn: hook (`is_intro`), ESCALATION/CLIMAX, twist; tối đa 3 clip, không hai clip liền nhau.
- **Đổi sang 9:16:** crop bám chủ thể bằng 2 keyframe (pan tuyến tính). Nếu phóng to quá 2× (ví dụ nguồn 4:3 720p) thì dùng contain + nền mờ. Detector chạy CPU, giấy phép sạch: YOLOX (Apache-2.0). **Tránh Ultralytics** (AGPL).
- **Gắn vào Stage 5 (đã test trên Mac):**
  - Shot clip có hợp đồng y hệt shot panel: h264 High@4.0, 1080×1920, 30 fps, yuv420p, không audio, đúng `round(dur×30)` frame. Ghép `-c copy` chạy đúng (225 frame / 7,5 s). Render mỗi clip ~0,6 s.
  - Thêm `clip_id` và `clip_meta` vào `Shot`; pass `_apply_clips_to_shots` chạy sau custom image.
  - Thứ tự ưu tiên: khoá tay > clip tự gán > panel.
  - Cache theo hash; khoá tay trong review UI (`lock_clip`); ghi credit vào `credits.json` để đưa vào mô tả YouTube.
- **Chi phí mỗi Short:** ≈ $0 (khớp bằng từ), hoặc ≤$0,002 nếu nhờ VLM xác nhận 3 lựa chọn.

## 5. Kiểm Content ID trước khi đăng
1. Upload **riêng tư** qua YouTube Studio và chờ bước **Checks** (kiểm bản quyền) chạy xong. Không đăng khi Checks còn chạy.
2. Studio → Content → tab Shorts → lọc "Claims".
3. Kiểm lại sau 24–48 giờ: kết quả Checks chưa phải cuối cùng.
4. Chưa có tài liệu xác nhận video riêng tư cũng bị Content ID quét. Làm thử một lần trước: upload riêng tư một clip có nhạc của hãng lớn để xem claim có hiện không.
5. Bị claim sai trên nội dung CC/PD thì dispute với lý do "có đủ quyền". Không dispute khi không chắc, vì dispute bị bác có thể thành cảnh cáo bản quyền.

## 6. MVP đề xuất (3–4 ngày, gần như $0)
1. Thư viện ~20 clip từ trailer CC-BY Mortal Kombat 1 / MultiVersus trên Commons (Batman, Superman, Joker, Omni-Man, Homelander), cộng tuỳ chọn 3 phim Superman Fleischer.
2. Duyệt tay từng clip: đúng nhân vật, không chữ/logo; ghi credit vào sổ.
3. Render 3 Short có clip ở hook. Thử bằng cách thay `shots/shot_NNN.mp4` rồi chạy lại assemble, chưa cần code mới.
4. Upload riêng tư → xem Checks/Claims → nếu sạch mới A/B trên kênh (ghép cặp chủ đề).
5. **Truyện Marvel:** thử xin Marvel Rivals Partner Hub, hoặc giữ panel động + SFX.

## 7. Chưa xác minh
- Chính sách video gameplay của Sony/Insomniac, WB Games, 2K, Kabam, Second Dinner, Square Enix (Guardians/Avengers), Capcom Video Guidelines (các trang trả 403).
- Giá mua clip từ studio.
- Hồ sơ gia hạn bản quyền của 17 phim Superman và serial Batman.
- Video riêng tư có bị Content ID quét không.
- Độ chính xác VLM với nhân vật siêu anh hùng.

## 8. Nguồn chính
- **YouTube Help:**
  - Content ID claim: https://support.google.com/youtube/answer/6013276
  - Shorts 3 phút: https://support.google.com/youtube/answer/15424877
  - Cảnh cáo bản quyền: https://support.google.com/youtube/answer/2814000
  - Hiểu lầm bản quyền: https://support.google.com/youtube/answer/2797449
  - Giấy phép CC: https://support.google.com/youtube/answer/2797468
  - Reused content: https://support.google.com/youtube/answer/1311392
  - Checks khi upload: https://support.google.com/youtube/answer/57407
  - Remix: https://support.google.com/youtube/answer/10623810
  - ToS: https://www.youtube.com/t/terms
- **YouTube Data API:**
  - Quota: https://developers.google.com/youtube/v3/determine_quota_cost
  - Developer Policies: https://developers.google.com/youtube/terms/developer-policies
- **Wikimedia Commons:**
  - Template "Free depiction (Mortal Kombat)": https://commons.wikimedia.org/wiki/Template:Free_depiction_(Mortal_Kombat)
  - Trailer Homelander: https://commons.wikimedia.org/wiki/File:Mortal_Kombat_1_%E2%80%93_Official_Homelander_Gameplay_Trailer.webm
  - Free depictions of non-free works: https://commons.wikimedia.org/wiki/Commons:Free_depictions_of_non-free_works
- **Internet Archive:** https://archive.org/details/fleischer-superman
- **Luật và giấy phép:**
  - CC BY 4.0: https://creativecommons.org/licenses/by/4.0/legalcode.en
  - CC FAQ: https://creativecommons.org/faq/
  - 17 U.S.C. §103: https://www.law.cornell.edu/uscode/text/17/103
  - Copyright Office Circular 15A: https://www.copyright.gov/circs/circ15a.pdf
- **Chính sách của hãng:**
  - PlayStation ToS: https://www.playstation.com/en-us/legal/terms-of-service/
  - Warner ToU: https://policies.warnerbros.com/terms/en-us/html/terms_en-us_1.5.2.html
  - Epic Fan Content Policy: https://www.epicgames.com/site/en-US/fan-art-policy
  - Capcom Fan Content Guidelines: https://www.capcom-games.com/en/fan-content-guidelines/
  - Marvel Rivals Partner Hub: https://www.marvelrivals.com/creator_program.html
  - Disney ToU: https://disneytermsofuse.com/english/
- **Công cụ:**
  - PySceneDetect: https://www.scenedetect.com/docs/latest/api/detectors.html
  - YOLOX: https://github.com/Megvii-BaseDetection/YOLOX
  - Ultralytics license: https://www.ultralytics.com/license
  - Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
