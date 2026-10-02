---
name: scout-moment
description: Scout single moments cho micro_moment mode (Short 30-50s, 1 cảnh + nghĩa). Dùng khi Master gõ /scout-moment hoặc muốn micro-moment mới.
---

# /scout-moment — tìm khoảnh khắc micro-moment tiếp theo

Spawn agent `moment-scout` (Agent tool, subagent_type: "moment-scout") — KHÔNG tự search trong main thread.

Prompt cho agent phải nhắc đủ:
1. Dedup/ban: `comic_candidates.csv` (produced/rejected/banned), `qa_question_banlist.md` (section Produced), `ls projects/`. Moment KHÁC từ issue đã produce chỉ nhận khi panel sẽ khác hẳn.
2. LATEST RELEASES RULE (BẮT BUỘC): Chỉ scout các issue/ongoing runs MỚI NHẤT xung quanh thời điểm scout (năm hiện tại hoặc late previous year, các issue vừa phát hành trong vài tháng qua). TUYỆT ĐỐI LOẠI BỎ truyện cũ từ 2024 trở về trước trừ khi Master có yêu cầu riêng. Khi web search phải chủ động kèm năm hiện tại hoặc các cụm "latest issues", "new comic releases", "ongoing run".
3. Gates: EMOTIONAL PARADOX GATE trong MỘT CẢNH (constant-bị-phá; cấm tả-thực thuần, cấm off-universe, cấm niche-only — số đo kênh nhà: tả-thực 19-1100v, off-universe 517v), fan-quoted có URL, 1 issue scrapable batcave, cảnh nhiều panel (subject main/near-main), meaning-in-one-sentence.
4. Title draft: NGẮN, khẳng định thẳng cú lật, meme-flip OK, không em-dash chain, không tên series nội bộ (bài học Bane "One Bad Day" 180v).
5. Output: bảng ranked ≤5 + command (stage_2 → set `target_moment` trong comic_context.json → `stages.stage_3 --mode micro-moment`). Bảng PHẢI có mô tả dài và chi tiết (detailed description):
   - Series, Issue & Year
   - Constant broken (quy tắc/hình tượng kinh điển bị phá vỡ)
   - What visibly happens (mô tả kỹ 3–5 câu chi tiết diễn biến từng khung hình: setup, va chạm, biểu cảm và visual payoff, TUYỆT ĐỐI KHÔNG tóm tắt 1 câu ngắn)
   - Why it lands (lý do cảnh này gây ấn tượng mạnh / fan tranh luận)
   KHÔNG ghi file.

Sau khi agent về: trình bảng, khuyến nghị 1 pick, chờ Master chọn. Produce xong → move comic vào CSV produced-banned.
