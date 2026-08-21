---
name: scout-moment
description: Scout single moments cho micro_moment mode (Short 30-50s, 1 cảnh + nghĩa). Dùng khi Master gõ /scout-moment hoặc muốn micro-moment mới.
---

# /scout-moment — tìm khoảnh khắc micro-moment tiếp theo

Spawn agent `moment-scout` (Agent tool, subagent_type: "moment-scout") — KHÔNG tự search trong main thread.

Prompt cho agent phải nhắc đủ:
1. Dedup/ban: `comic_candidates.csv` (produced/rejected/banned), `qa_question_banlist.md` (section Produced), `ls projects/`. Moment KHÁC từ issue đã produce chỉ nhận khi panel sẽ khác hẳn.
2. Gates: EMOTIONAL PARADOX GATE trong MỘT CẢNH (constant-bị-phá; cấm tả-thực thuần, cấm off-universe, cấm niche-only — số đo kênh nhà: tả-thực 19-1100v, off-universe 517v), fan-quoted có URL, 1 issue scrapable batcave, cảnh nhiều panel (subject main/near-main), meaning-in-one-sentence.
3. Title draft: NGẮN, khẳng định thẳng cú lật, meme-flip OK, không em-dash chain, không tên series nội bộ (bài học Bane "One Bad Day" 180v).
4. Output: bảng ranked ≤5 + command (stage_2 → set `target_moment` trong comic_context.json → `stages.stage_3 --mode micro-moment`). KHÔNG ghi file.

Sau khi agent về: trình bảng, khuyến nghị 1 pick, chờ Master chọn. Produce xong → move comic vào CSV produced-banned.
