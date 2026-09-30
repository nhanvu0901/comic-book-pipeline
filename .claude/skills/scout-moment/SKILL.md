---
name: scout-moment
description: Scout single moments cho micro_moment mode (Short 30-50s, 1 cảnh + nghĩa). Dùng khi Master gõ /scout-moment hoặc muốn micro-moment mới.
---

# /scout-moment — tìm khoảnh khắc micro-moment tiếp theo

Spawn agent `moment-scout` (Agent tool, subagent_type: "moment-scout"). Agent trả lời tiếng Việt.

Prompt cho agent phải nhắc đủ:
1. Dedup/ban: `comic_candidates.csv` (produced/rejected/banned), `qa_question_banlist.md` (section Produced), `ls projects/`. Moment KHÁC từ issue đã produce chỉ nhận khi panel sẽ khác hẳn.
2. Gates: một cảnh hoặc chuỗi rất hẹp trong MỘT issue; setup dễ hiểu → hành động/quyết định/lời tiết lộ cụ thể (turning point) → hệ quả trực tiếp. Với recap dài của đối thủ, chỉ lấy một điểm chuyển tự đứng được trong 35–50 giây. Constant bị phá, visual lớn, fan quote, hai tên lớn là điểm cộng, không bắt buộc.
3. Search cả issue mới và evergreen; chỉ giới hạn năm khi Master yêu cầu. Ưu tiên nhân vật dễ nhận, payoff hiểu được với một câu ngữ cảnh. Xác minh series/volume/issue/year, comic chứ không phải adaptation, nguồn cho turning point và hệ quả, batcave scrapable. Video đối thủ và fan post là tín hiệu tìm kiếm, không thay nguồn truyện. Coverage tiếng Anh là tín hiệu xếp hạng, không loại cứng.
4. Title draft: NGẮN, gọi tên nhân vật và nói thẳng tình huống; tránh tên series nội bộ và mô tả mơ hồ như "đấu trí", "kiểm soát nhịp độ" nếu không chỉ ra hành động.
5. Output: bảng ranked ≤5 với setup → turning point → hệ quả, issue/năm, nguồn, batcave URL, coverage, lý do chọn; kèm command (stage_2 → set `target_moment` trong comic_context.json → `stages.stage_3 --mode micro-moment`). Không bịa thứ tự panel, biểu cảm, động cơ hay chi tiết chưa có nguồn. KHÔNG ghi file.

Xem `MICRO_MOMENT_REFERENCE_AUDIT_2026-09-30.md` để hiểu năm kiểu điểm chuyển rút từ năm kênh đối thủ.

Sau khi agent về: trình bảng, khuyến nghị 1 pick, chờ Master chọn. Produce xong → move comic vào CSV produced-banned.
