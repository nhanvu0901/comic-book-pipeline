---
name: scout-qa
description: Scout Q&A questions cho explore_answer mode (mode chính của kênh). Dùng khi Master gõ /scout-qa hoặc muốn câu hỏi Q&A mới.
---

# /scout-qa — tìm câu hỏi Q&A tiếp theo

Spawn agent `qa-question-scout` (Agent tool, subagent_type: "qa-question-scout") — KHÔNG tự search trong main thread.

Prompt cho agent phải nhắc đủ:
1. Đọc `qa_question_banlist.md` (CẢ section "Produced — đã làm video") + `qa_question_bank.md` (candidate treo được lấp gap) + `ls projects/`.
2. Gates: EMOTIONAL PARADOX GATE (A-tier + hằng-số-bị-phá; cấm dead-formula), SPECIFICITY BAND, PANEL DENSITY, multi-source 2010+, coverage EN-only disqualify.
3. Cả 2 shape: LIST ("Who has actually...?") và EXPLAINER ("This is how [A-tier]..." / "Why does X always...?" — statement giữ dấu chấm).
4. Output: bảng ranked ≤5 + command answer_pipeline cho top pick. KHÔNG ghi file bank/banlist — Master duyệt mới ghi.

Sau khi agent về: trình bảng cho Master (giữ nguyên cột year/issue/URL/coverage/verdict), khuyến nghị 1 pick, chờ Master chọn. Nhớ rule: scout's "verified" vẫn phải re-verify khi produce; produce xong → move vào banlist section Produced + xóa khỏi bank.
