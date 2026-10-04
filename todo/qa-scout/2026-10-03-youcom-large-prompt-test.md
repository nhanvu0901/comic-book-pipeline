# You.com Research API: prompt lớn có làm mất ngữ cảnh không? — đo 2026-10-03

Máy Mac, `POST https://api.you.com/v1/research` (endpoint scout đang dùng), whitelist 8 domain như scout.
Script, response thô, prompt đã gửi, bảng kết quả: thư mục scratchpad `youcom_ctx/` của phiên (`e1_results.json`, `e2_table.csv`, `e3_table.csv`, `e3_summary.json`, `raw/`, `prompts/`, `ledger.jsonl`). Thư mục đó là tạm.
Nhãn: [doc] có trong tài liệu chính thức · [đo] tự đo · [suy luận].

## 1. Tài liệu nói gì

| Câu hỏi | Tài liệu | Nguồn |
|---|---|---|
| Giới hạn input | "max 40,000 characters"; OpenAPI `"maxLength": 40000` | you.com/docs/guides/research ; you.com/docs/openapi/research.json |
| Vượt giới hạn thì sao | không nói (bảng lỗi chỉ có 422 chung) → đã đo ở E1 | you.com/docs/using-the-api/error-code-reference |
| Agent đọc toàn bộ input hay tóm tắt/cắt | không nói | — |
| Model / context window theo effort | không nêu; chỉ nói dùng "context-masking and compaction", effort cao chạy >1.000 lượt suy luận, xử lý tới 10 triệu token | you.com/docs/guides/research |
| Vị trí chỉ dẫn, danh sách loại trừ | không có hướng dẫn; loại trừ duy nhất là `exclude_domains` | you.com/docs/guides/research/source-control |
| `warnings` | "source access issues or partial results"; không nói gì về input dài | OpenAPI `ResearchOutput` |
| Độ trễ | lite < 10 s, standard 10–30 s, deep < 120 s | you.com/docs/guides/research |

→ Docs không trả lời được câu "có bỏ sót ngữ cảnh không", nên phải đo.

## 2. Thí nghiệm

### E1 — giới hạn độ dài (lite, dòng mã kiểm tra đặt ở CUỐI prompt)
| Ký tự | Byte | Kết quả |
|---|---|---|
| 39.900 | 39.900 | 200, đọc đúng mã |
| 40.000 | 40.000 | 200, đọc đúng mã |
| 40.100 | 40.100 | **422 `string_too_long`** |
| 39.990 | 40.725 | 200, đọc đúng mã → giới hạn tính theo **ký tự**, không theo byte |

Vượt giới hạn thì báo lỗi rõ ràng, **không cắt ngầm**. Lưu ý: body 422 lặp lại nguyên cả prompt, nên đừng log nguyên body.

### E2 — model có thấy toàn bộ prompt không (standard; giấu mã kiểm tra theo cỡ × vị trí)
Phần đệm là dòng thật của banlist và `comic_candidates.csv` (không cắt 9.000), bố cục giống scout (danh sách trước, nhiệm vụ cuối).

| Cỡ | Đầu | Giữa | Cuối |
|---|---|---|---|
| ~3k | đúng | đúng | đúng |
| ~12k | đúng | đúng | đúng |
| ~24k | đúng | đúng | đúng |
| ~38k | đúng | đúng | đúng |

12/12 đúng; `warnings` luôn rỗng; độ trễ 8,8–14,7 s, không tăng theo cỡ.

### E3 — danh sách loại trừ có được tuân thủ không (standard)
Câu hỏi "Every time Batman broke his no-kill rule", xin tối đa 10 issue. 6 item hay bị trả lại có mặt trong mọi danh sách: Final Crisis #6, Batman #57 (2018), Dark Nights: The Batman Who Laughs #1, Batman #420, #425, Annual #8. Phần đệm là mục tổng hợp, không phải Batman. Chấm theo (series, số issue), bỏ qua năm.

| Độ dài danh sách | Số lượt | Item bị cấm quay lại (TB /6) | % ứng viên là mục bị cấm | Ứng viên hợp lệ TB |
|---|---|---|---|---|
| 0 (đối chứng) | 3 | **3,67** | 45,8% | 4,3 |
| 10 | 4 | 1,00 | 12,5% | 7,0 |
| 50 | 4 | 1,00 | 14,8% | 5,75 |
| 200 | 6 | 2,00 | 27,3% | 5,3 |
| 800 | 2 | 2,50 | 38,5% | 4,0 |

- Đặt danh sách ở đầu hay cuối prompt không khác nhau: 1,62 so với 1,50.
- `deep` không tốt hơn `standard` (mỗi ô chỉ 1 lượt).
- Có danh sách so với đối chứng: giảm 57%, p ≈ 0,010. Danh sách 10–50 mục so với 200–800 mục: p ≈ 0,049.
- **Model thấy danh sách nhưng vẫn vi phạm.** 15/15 lượt vi phạm đều tự khai trong `notes` là "đã loại các mục already-used".
- Theo từng item:
  - Batman Annual #8 vẫn quay lại 11/16 lượt có danh sách.
  - Final Crisis #6 bị chặn gần hết: 3/3 lượt đối chứng, chỉ 2/16 lượt có danh sách.

## 3. Trả lời
- **Không mất ngữ cảnh** trong phạm vi đo, kể cả ở 38k ký tự và mọi vị trí. Vượt 40.000 ký tự thì báo lỗi 422, không cắt ngầm.
- **Danh sách loại trừ có tác dụng một phần, và ngắn thì tốt hơn dài.**
  - Đối chứng: 46% ứng viên là mục bị cấm.
  - Danh sách ≤50 mục: còn 12–15%.
  - Danh sách 200 mục: còn 27%.
  - Danh sách 800 mục: còn 38%.

  Không bao giờ về 0. Đây là **lỗi tuân thủ**, không phải lỗi bỏ sót ngữ cảnh.

## 4. Hệ quả cho thiết kế
1. Thêm chốt `len(prompt) <= 40_000` ký tự. Bỏ `digest[:9000]`: cái cắt đó không cần vì lý do ngữ cảnh, mà lại đang cắt mất HARD RULES (TODO Q&A #1).
2. Vị trí đặt luật không tạo khác biệt đo được. Ghim luật cứng ngắn ở chỗ cố định; đừng kỳ vọng vị trí sửa được lỗi tuân thủ.
3. **Giữ danh sách tránh nhưng nhỏ: ≤50 mục liên quan nhất (~1,8k ký tự), không dán cả ~230 dòng.** Kết quả này khớp với thiết kế sổ cái (top-K liên quan). Nó cũng cho thấy chưa nên bỏ hẳn danh sách tránh: nhánh "không gửi gì" của Phụ lục A sẽ lặp nhiều hơn, trừ khi điều hướng theo ô bù được.
4. Code vẫn là hàng rào cứng: khoá series chuẩn hoá + số issue, bỏ năm vì model hay ghi sai năm. Lấy dư theo tỷ lệ sống sót đo được: ~85% khi có danh sách ≤50, ~54% khi không có danh sách.
5. Không dựa vào `warnings` hay lời tự khai trong `notes` để biết model có tuân thủ.

## 5. Chi phí
39 lượt: 4 lite, 31 standard, 4 deep. Cận trên ≈ **$2,00**, trong trần $3.

## 6. Giới hạn
- Cỡ mẫu rất nhỏ (1–3 lượt mỗi ô); p-value chỉ mang tính chỉ báo.
- Chỉ một câu hỏi và một bộ item hay bị trả lại chọn sẵn.
- Phần đệm là mục tổng hợp, không cùng chủ đề Batman.
- E2 là phép thử dễ (có dấu hiệu rõ); chưa đo chất lượng nhiệm vụ chính khi prompt lớn.
- Chưa thử đặt danh sách ở giữa, và chưa thử các biện pháp giảm nhẹ (bảo model đối chiếu từng ứng viên với danh sách trước khi trả lời, nhắc luật ở hai đầu).
- Chưa xác minh trên hoá đơn xem lượt 422 có bị tính tiền không.
