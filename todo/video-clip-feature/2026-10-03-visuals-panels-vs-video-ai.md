# Hình ảnh cho Shorts: panel comic, clip phim/hoạt hình, hay AI? — nghiên cứu 2026-10-03

Hai nhánh nghiên cứu (Sonnet, có link nguồn): (1) sức hút và luật/chính sách YouTube; (2) kỹ thuật, giá, tích hợp Stage 5. Chưa sửa code.
Nhãn: [doc] = tài liệu chính thức · [2nd] = báo chí/blog · [suy] = suy luận.

## 1. Kết luận

| Phương án | Sức hút | Rủi ro bản quyền / Content ID | Rủi ro kiếm tiền (YPP) | Chi phí / Short 45 s | Kết luận |
|---|---|---|---|---|---|
| **A. Panel + Ken Burns (hiện tại)** | Cơ bản | Trung bình (ít bị claim tự động) | Trung bình, nếu thiếu góc nhìn riêng | $0 | Giữ làm nền |
| **B. Clip phim/hoạt hình** | Chưa có dữ liệu; một kênh tương đương tụt <1k view (n=1) | **Cao nhất**: studio có bản gốc trong Content ID, bắt tự động | **Cao**: reused content, cộng đợt giảm phân phối Shorts đăng lại từ 01/10/2026 | $0 API + 30–90 phút người | **Bỏ** |
| **C. AI vẽ cảnh/nhân vật** | Không chắc; feed đã bão hoà AI, fandom ghét AI | **Cao**: Disney/Universal/WB kiện Midjourney; Disney buộc Google gỡ video AI Iron Man, Deadpool khỏi Shorts | Trung bình–cao: inauthentic/mass-produced | ~$5–50 | Chỉ dùng cho b-roll không có nhân vật |
| **D. Làm động chính panel thật** (I2V 2–4 shot hero + parallax) | Có thể tăng nhịp; chưa có dữ liệu | Gần bằng A, nhưng model AI có thể từ chối ảnh Marvel | Trung bình; tránh hiệu ứng giống hệt nhau mọi video | ~$1,5–6 | **Thử**, sau probe |

**Không có bằng chứng có kiểm soát nào cho thấy panel tĩnh làm mất view trên Shorts.**
- Các con số "Ken Burns tăng 15–30% thời gian xem" trên blog đều không có dữ liệu gốc.
- Bằng chứng nội bộ cho thấy chủ đề và tiêu đề tạo chênh lệch 10–25 lần view trong cùng một kênh. Đối thủ 15M view dùng panel, cắt nhanh và caption nhảy chữ, không dùng AI video.
- → Hình ảnh nhiều khả năng là **đòn bẩy biên**. Hook, chủ đề và tiêu đề mới là đòn bẩy chính.

## 2. Luật và chính sách (nhánh 1)
- **Content ID không phán fair use.** Theo EFF, dưới 1% claim bị tranh chấp, và tranh chấp bị bác có thể thành cảnh cáo bản quyền. 3 cảnh cáo trong 90 ngày thì kênh bị xoá, và tài khoản cùng các kênh liên kết cũng có thể bị xoá theo. → Thử trên kênh phụ cùng tài khoản **không an toàn**.
- **Shorts có claim:** trước đây, Short dài hơn 1 phút có claim bị chặn toàn cầu. Từ 24/09/2026 không còn tự động chặn nữa; tình trạng kiếm tiền sau thay đổi này chưa rõ.
- **YPP:**
  - Từ 15/07/2025, "repetitious content" đổi tên thành "inauthentic content" (nội dung sản xuất hàng loạt, theo khuôn mẫu).
  - Đợt cập nhật Shorts 10/2026 ghi rõ "VO mô tả những gì đang xảy ra trên màn hình" và "thay đổi hàng loạt theo template" **không** tính là nội dung gốc; "bình luận, phân tích, tường thuật riêng" thì tính.
  - Tháng 01/2026 YouTube xoá 16 kênh "AI slop" (4,7 tỷ view); tháng 12/2025 chấm dứt các kênh trailer AI giả.
- **Nhân vật do AI tạo:**
  - Vụ Disney/Universal kiện Midjourney (06/2025) và WBD kiện Midjourney (09/2025) đang ở giai đoạn discovery.
  - Disney gửi thư yêu cầu ngừng cho Google (12/2025), kèm danh sách nhân vật cần gỡ khỏi YouTube/Shorts, có Iron Man và Deadpool.
  - Gemini chặn nhân vật Disney. Thoả thuận Disney–Sora đã huỷ, Sora đã đóng. Hiện **không có kênh AI hợp pháp nào cho nhân vật Marvel**.
  - Nội dung hoàn toàn do AI tạo không được bảo hộ bản quyền (Thaler).
- **Nhãn AI:**
  - Bắt buộc khai báo với nội dung giống thật; nội dung hoạt hình/phi thực tế được miễn.
  - Từ 27/05/2026 YouTube tự gắn nhãn AI photoreal (đọc C2PA/SynthID). Video tạo bằng Veo luôn có nhãn.

## 3. Kỹ thuật và giá (nhánh 2)
- **Giá image-to-video:** khoảng $0,04–0,40/giây, ví dụ:
  - Wan 2.6 Flash $0,05; Runway Gen-4 Turbo $0,05;
  - MiniMax H3 $0,08; Gemini Omni Flash ~$0,10; Veo 3.1 Fast $0,10–0,12; Kling 3.0 $0,11–0,17;
  - Seedance 2.0 ~$0,24–0,30; Veo 3.1 Std $0,40.
- **Sora 2 API đã tắt ngày 24/09/2026.**
- **Guardrail bản quyền là rủi ro số 1 của phương án D:**
  - Tài liệu Gemini Omni ghi rõ chặn "copyrighted content".
  - Google/Veo gần như chắc chắn từ chối nhân vật Disney/Marvel.
  - Seedance 2.5 chặn IP.
  - MiniMax đang bị Disney/Universal/WBD kiện.
  - Kling, Wan, Runway, Luma, Pika: không có dữ liệu công khai → **phải tự probe**.
  - Nên dùng tài khoản và API key riêng cho video, tách khỏi key Gemini/OpenRouter đang chạy pipeline.
- **Chạy local không khả thi:** server Windows là CPU + iGPU AMD 780M, không có CUDA; Mac chỉ 16 GB; Wan 2.2 5B cần ≥24 GB VRAM. → Dùng API.
- **Chất lượng khi animate tranh comic:** chưa có benchmark. Kỳ vọng:
  - frame đầu giữ đúng nét vẽ và màu, vì panel thật là điều kiện mạnh nhất;
  - sau 1–2 giây dễ trôi sang 3D, halftone nhấp nháy, tay/mặt méo khi chuyển động lớn;
  - vùng an toàn: clip ≤5 s, chuyển động nhỏ (tóc, áo choàng, khói, đẩy camera).
- **Parallax 2.5D không cần AI nặng, $0:**
  - Depth Anything V2 Small hoặc DA3 Small/Base (Apache-2.0; các bản lớn hơn là phi thương mại) + DepthFlow;
  - hoặc tự làm 2 lớp từ `char_boxes` của Magi + LaMa (đã cài).
  - Lưu ý: model độ sâu học trên ảnh thật nên thường yếu với tranh nét phẳng.
- **Clip phim:**
  - Kỹ thuật làm được: PySceneDetect chia cảnh, Gemini khớp cảnh với beat, crop 9:16.
  - Nhưng hầu hết issue 2010+ không có bản hoạt hình cùng cảnh, nên dùng clip "cùng nhân vật, khác cảnh" sẽ phá nguyên tắc bám beat.
- **Shot hiện tại rất ngắn:** recap 1–2 s/shot, micro ≤5 s, trong khi vendor tính tối thiểu 3–5 s mỗi clip. → AI chỉ đáng tiền cho shot ≥2,5 s.

## 4. Tích hợp vào Stage 5 (nếu thử D)
- **Schema** (`stages/stage_5/schema.py`): thêm `motion_source: "panel" | "parallax" | "ai_i2v"` (mặc định `panel`) và `ai_prompt`.
- **Kế hoạch shot:** thêm `_apply_motion_plan` cạnh `_apply_custom_images_to_shots` (shots.py ~1426).
- **Chọn shot hero:** cold-open, shot hành động (`_is_action_text`), shot ≥2,5 s, hoặc Master khoá trong review. Trần `AI_I2V_MAX_SHOTS` mặc định **0 (tắt)**, cộng `AI_I2V_BUDGET_USD`.
- **Hook render:** trong `render_shot`, ngay sau khi có khung 1080×1920 (shots.py ~3030), trước `zoompan`.
  - Lỗi, bị từ chối, timeout hay vượt ngân sách thì **rơi về panel Ken Burns**, theo đúng mẫu `_ai_upscale_panel` đang có.
- **Hợp đồng đầu ra:** mp4 1080×1920, 30 fps, h264 yuv420p, không audio, đủ `round(dur×30)` frame, vì `_concat` ghép bằng `-c copy`.
- **Cache theo hash** (PNG + prompt + model + thông số) trong `projects/<p>/shots/_ai/`. `force` không xoá cache, chỉ cờ `--regen-ai` mới xoá.
- **Sinh trước, song song:** pre-pass trong `assemble_project` (pipeline.py ~190).
- **Log:** vendor, chi phí, lý do fallback vào `shots.json`.

## 5. Đề xuất thử (thứ tự)
1. **Probe từ chối và độ trung thực** (≤1 ngày, ~$15–30):
   - 6 panel đã xoá bong bóng thoại (2 Marvel, 2 DC, 2 Image/indie);
   - 5–7 endpoint: Kling v3, Wan 2.6 Flash, Veo 3.1 Fast, Seedance 2.0, H3, Runway Gen-4 Turbo, Gemini Omni.
   - Ghi: chấp nhận hay từ chối, chi phí, độ trễ, điểm 1–5 cho nét vẽ/màu/nhân vật/chuyển động.
   - **Nếu mọi vendor đều chặn cả Marvel lẫn DC, bỏ AI và chuyển sang parallax.**
2. **"A+" không cần AI:** parallax nhẹ cho shot dài, cắt nhịp nhanh hơn, hiệu ứng âm thanh, caption động. Biến thể hoá nhịp giữa các video để tránh nhãn "template".
3. **"D-hook":** chỉ làm động 2–4 giây đầu, vì người xem quyết định lướt hay ở lại trong vài giây đầu.
   - Làm 3 Short mà **không cần sửa code**: render bình thường, thay `shots/shot_NNN.mp4` của 2–3 shot bằng clip AI đã chuẩn hoá, xoá `video_silent.mp4` và `final.mp4`, rồi chạy lại assemble.
4. **A/B trên kênh** (YouTube chưa hỗ trợ A/B cho Shorts):
   - Ghép cặp chủ đề tương đương, chia ngẫu nhiên, đăng cùng khung giờ; không đăng hai bản của cùng một câu chuyện.
   - Đo tỷ lệ xem/lướt, thời gian xem trung bình, engaged views; so đường retention ngay tại giây phát shot AI với các giây kề bên.
   - Dừng ngay khi có cảnh cáo bản quyền, hoặc 2 claim trong 5 video đầu.
   - Upload ở chế độ riêng tư trước để xem kết quả kiểm tra bản quyền.
5. **Nơi thử rủi ro thấp nhất:** `art_pipeline` (tranh CC0 của The Met), vì không có bản quyền nhân vật.
6. **Không cần tốn tiền:** tự xem 10 Short top và 10 Short đáy của mỗi kênh đối thủ, gắn nhãn kiểu hình ảnh (agent không xem được trang YouTube) để kiểm giả thuyết "panel tĩnh mất view".

## 6. Nguồn chính
- **YouTube Help:**
  - reused/inauthentic content: https://support.google.com/youtube/answer/1311392
  - khai báo nội dung AI: https://support.google.com/youtube/answer/14328491
  - Content ID: https://support.google.com/youtube/answer/6013276
  - fair use: https://support.google.com/youtube/answer/9783148
  - cảnh cáo bản quyền: https://support.google.com/youtube/answer/2814000
  - A/B (không hỗ trợ Shorts): https://support.google.com/youtube/answer/16391400
- **Cập nhật Shorts 10/2026:**
  - https://www.searchenginejournal.com/youtube-original-shorts-reposted-clips-reach/591774/
  - https://www.socialmediatoday.com/news/youtube-updates-shorts-algorithm-to-put-more-focus-on-original-content/831976/
- **Nhãn AI 05/2026:** https://9to5google.com/2026/05/27/youtube-updating-ai-content-labels/
- **EFF về Content ID:** https://www.eff.org/wp/unfiltered-how-youtubes-content-id-discourages-fair-use-and-dictates-what-we-see-online
- **Án lệ:**
  - Hosseinzadeh v. Klein: https://www.copyright.gov/fair-use/summaries/hosseinzadeh-klein-sdny2017.pdf
  - Dr. Seuss v. ComicMix: https://law.justia.com/cases/federal/appellate-courts/ca9/19-55348/19-55348-2020-12-18.html
- **Vụ kiện và hành động của Disney:**
  - Disney/Universal v. Midjourney: https://itsartlaw.org/art-law/framing-the-future-disney-and-universal-challenge-midjourney-over-ai-generated-imagery/
  - Disney gửi thư cho Google: https://variety.com/2025/digital/news/disney-google-ai-copyright-infringement-cease-and-desist-letter-1236606429/
  - Gemini chặn Disney: https://www.androidauthority.com/google-gemini-blocks-disney-generation-3639914/
- **Giá:**
  - Gemini/Veo: https://ai.google.dev/gemini-api/docs/pricing
  - Gemini Omni: https://ai.google.dev/gemini-api/docs/omni
  - Runway: https://docs.dev.runwayml.com/guides/pricing/
  - Luma: https://lumalabs.ai/api/pricing
  - MiniMax: https://platform.minimax.io/docs/guides/pricing-paygo
  - fal (Kling, Seedance, Wan): https://fal.ai/models
  - Sora ngừng: https://developers.openai.com/api/docs/deprecations
- **Công cụ:**
  - Depth Anything V2: https://github.com/DepthAnything/Depth-Anything-V2
  - Depth Anything 3: https://github.com/ByteDance-Seed/Depth-Anything-3
  - DepthFlow: https://github.com/BrokenSource/DepthFlow
  - Wan 2.2: https://github.com/Wan-Video/Wan2.2
  - PySceneDetect: https://github.com/Breakthrough/PySceneDetect
  - RIFE: https://github.com/nihui/rife-ncnn-vulkan
- **Bảng xếp hạng I2V:** https://artificialanalysis.ai/video/leaderboard/image-to-video
