# Kết quả cập nhật Q&A writer — 2026-11-03

Đã áp dụng vào workspace chính, do Terra viết code, root điều phối và kiểm tra, một Terra khác review độc lập.

- `prompts/gemini_qa_writer_template.md`: hợp đồng hook riêng cho LIST/DIRECT; câu trả lời DIRECT kết hợp kết luận có nguồn trong item cuối và kết vào kết quả item đó; không thêm recap. Khái quát ANSWER/CONTEXT/DEVELOPMENT/RESULT thay ngôn ngữ construct.
- `stages/stage_3/gemini_prompt.py`: ưu tiên format project/session đã xác định, hỗ trợ legacy explain, thiếu format thì dùng helper hiện có trên cùng question được render. Có fallback comic title/project name.
- Tổng 110–160 từ gồm hook là mục tiêu mềm; bỏ chỉ dẫn 40–60 từ/item; chia theo độ phức tạp nguồn và cho phép dài hơn khi cần đủ item theo ràng buộc importer. Không pad hoặc bịa để đủ từ.
- Giữ item order, một paragraph/item, tiếng Anh, fidelity/grounding, comic + issue sau hook; micro template và parser không đổi.

## Kiểm tra

Terra tái hiện RED10 ca; baseline119 passed. Sau triển khai129 passed. Bổ sung review15 contract tests passed. Root áp dụng đúng diff vào workspace chính và chạy:

`pytest -q tests/test_gemini_qa_writer_contract.py tests/test_gemini_script_order_lock.py tests/test_gemini_micro_script_parse.py tests/test_gemini_micro_moment_prompt.py tests/test_qa_dual_format_flow.py --tb=short`

**134 passed in 10.11s.** Review toàn thay đổi và scoped re-review: approved, hai minor đã giải quyết. `git diff --check` được kiểm tra riêng.

Full suite tùy chọn của implementer đã dừng sau423 passed/279 warnings, không phải full-suite pass. Không có live LLM/TTS benchmark hoặc chứng minh tăng view; test xác nhận hợp đồng prompt và luồng nhập script.

Commit tham chiếu trên nhánh `codex/qa-writer-contract-2026-11-03`: `6276e50` và `9fbf4dd`. Code đã đưa về working tree chính; root chưa commit/push thay đổi ở main.

## Quyết định triển khai

- User đã yêu cầu triển khai ba vấn đề trong báo cáo; giữ phạm vi thay đổi nhỏ và không rewrite các prompt khác.
- Cho phép kịch bản nhiều item vượt soft target để giữ dữ kiện và importer layout; hệ quả có thể là video dài hơn, cần đo bằng voice thực tế.
- Một thay đổi duy nhất được review như toàn bộ changeset; phần chỉnh bổ sung được re-review riêng.


## Triển khai Windows server theo yêu cầu tiếp theo

Đã triển khai đúng ba file writer/generator/test vào `D:\code\comic-book-pipeline` trên `winbox-lan` (192.168.2.63). SHA-256 cả ba file khớp bản local đã kiểm tra. Không pull/reset repo vì server có các chỉnh sửa riêng trong UI/renderer; các file đó được giữ nguyên.

Backup trước triển khai: `D:\code\cbp-deploy-backups\qa-writer-20261103-112415`.

Windows chạy bộ134test:133passed,1failed ở discovery (`test_discovery_proposes_formats_with_evidence`). Đã xác định test sử dụng sổ chống lặp production: câu hỏi Thor/Mjolnir trùng mục `projects/mjolnir-shattered` nên bị loại. Cùng test đạt khi cô lập `question_avoid_lines` bằng danh sách trống trong tiến trình test, không sửa sổ production hoặc test file. Các test hợp đồng writer mới đều đạt trong lượt kiểm tra Windows. Không báo134/134pass ở môi trường production gốc.

UI trước triển khai không lắng nghe8550. Đã khởi động Scheduled Task `ComicUI` hiện có; listener8550 thuộc Python `-m ui --lan`, PID2964 tại lúc kiểm tra. Sau khi SSH kết thúc, máy Mac gọi `http://192.168.2.63:8550/` nhận HTTP200. File prompt đã lưu ở các project không bị viết lại; tạo lại writer prompt trong UI để dùng template mới.
