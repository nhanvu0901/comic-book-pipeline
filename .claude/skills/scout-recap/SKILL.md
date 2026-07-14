---
name: scout-recap
description: Scout one-shot/mini comics cho recap mode (60-76s kể cả truyện). Dùng khi Master gõ /scout-recap hoặc muốn comic recap mới.
---

# /scout-recap — tìm comic recap tiếp theo

Spawn agent `comic-scout` (Agent tool, subagent_type: "comic-scout") — KHÔNG tự search trong main thread.

Prompt cho agent phải nhắc đủ:
1. Dedup/ban: `comic_candidates.csv` (mọi row rejected/banned/produced — RULE 2026-07-10: produce xong là ban) + `ls projects/`. Backup treo sẵn trong CSV giữ nguyên, tìm lane mới.
2. Gates: one-shot ưu tiên (22-45pp), 2010+, scrapable batcave (verify LIVE bằng `chapters[].pages` — `reader["images"]` có bug rỗng), NO-COVERAGE (chỉ EN narration disqualify; deep-search Comicstorian/Comics Explained/"in minutes"/"part N"), rule 10-mainstream + 2e, twist ending verify qua nhiều review, seed từ best-of curation.
3. Ưu tiên chất liệu hợp title meme-flip + nghịch lý cảm xúc (data 2026-07-10: tả-thực thuần chết, kể cả A-tier).
4. Output: bảng ranked ≤5 — BẮT BUỘC đủ cột: Title | year | pages | #issues | has-Short? | content/hook | batcave URL | verdict. KHÔNG ghi CSV — Master duyệt mới ghi.

Sau khi agent về: trình bảng, khuyến nghị 1 pick, chờ Master chọn. Lưu ý chiến lược 2026-07-10: Q&A là mode chính — recap chỉ produce khi Master gọi đích danh.
