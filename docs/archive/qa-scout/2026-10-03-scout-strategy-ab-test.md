# A/B các chiến lược scout You.com — đo 2026-10-03 (Mac, không sửa code pipeline)

Dữ liệu (tạm, nằm trong scratchpad của phiên): thư mục `ab_scout/`, gồm
- 240 response thô (`raw/`), prompt đã gửi (`prompts/`), plan (`plans/`), sổ chi phí `ledger.jsonl`;
- bảng `tables/` (`rows.csv`, `calls.csv`, `arms.csv`, …) và script `lib.py`, `run_seed.py`, `analyze.py`.

Chi phí: **$9,65** (29 deep, 124 standard, 105 Search, 32 OpenRouter); trong đó khoảng $1,3 là các lượt phải chạy lại.

## Setup
- **6 seed:** Q&A (Batman không giết, Wolverine mất hồi phục, ai nhấc Mjolnir, Deadpool giết anh hùng nào) và micro (Hulk, Invincible).
- **Planner:** gọi một lần cho mỗi seed rồi đóng băng, để mọi nhánh của seed đó chỉ khác nhau ở đúng điều đang test.
- **"Đã làm" giả lập** cho mỗi seed: kết quả một lượt hiệu chỉnh `deep` theo đúng prompt production, các item hay bị trả lại, và các mục banlist/CSV liên quan. Khoá so khớp = series chuẩn hoá + số issue, bỏ năm.
- **"Mục mới hợp lệ":**
  - Q&A: đúng một issue, xuất bản từ 2010, không phải phim/game, chưa có trong "đã làm".
  - Micro: qua bộ lọc micro của pipeline.
- **NVI/$:** số mục mới hợp lệ trên mỗi đô la. "Cited" nghĩa là thêm điều kiện trích dẫn được pipeline chấp nhận; đây là số production thực sự dùng được.

## Kết quả (gộp 6 seed, 1–2 lượt lặp)

| Nhánh | Cách tìm | Lượt | $ | Tỷ lệ lặp ρ | Mục mới / lượt | NVI/$ | NVI cited/$ | Trích dẫn hợp lệ | Độ trễ |
|---|---|---|---|---|---|---|---|---|---|
| A | Như hiện tại (digest 9k, deep) | 8 | 0,80 | 0,62 | 0,75 | 7,5 | 5,0 | 0,76 | 31 s |
| B0 | Chỉ luật cứng, standard (đối chứng) | 6 | 0,30 | 0,76 | 0,83 | 16,7 | 6,7 | 0,64 | 22 s |
| B | Luật cứng + danh sách ≤50, deep | 12 | 1,20 | 0,25 | 2,4 | 24,2 | 19,2 | 0,77 | 29 s |
| B2 | Luật cứng + danh sách ≤50, standard | 12 | 0,60 | 0,14 | 2,0 | 40,0 | 33,3 | 0,79 | 23 s |
| **X** | **B2 + 6 domain thêm** | 12 | 0,60 | 0,12 | **3,0** | **60,0** | **45,0** | 0,76 | 22 s |
| C | 3 ô song song, standard, không danh sách | 12 | 1,80 | 0,20 | 2,1 | 13,9 | 12,8 | 0,95 | 26 s |
| C+ | 3 ô + danh sách | 9 | 1,35 | 0,00 | 2,2 | 14,8 | 14,1 | 0,95 | 25 s |
| D | Search gom lead → lọc tại chỗ → Research kiểm | 12 | 1,09 | 0,19 | 1,7 | 18,3 | 13,7 | 0,78 | 118 s |

Mục mới không trùng nhau, cộng qua các lượt lặp: A 5 · B 23 · B2 19 · B0 5 · C 19 · C+ 17 · D 17 · X 29.

## Trả lời
- **Câu 5, mở rộng domain (X so với B2):**
  - Nhiều hơn khoảng +30–50% mục mới mỗi đô la (3,0 so với 2,0 mục mỗi lượt; thắng 4/6 seed, chưa đủ ý nghĩa thống kê).
  - 20/36 mục mới đến từ **comicbook.com và screenrant.com**. image.fandom, wikipedia, gamerant và popverse không đóng góp mục nào.
- **Câu 6, 3 lượt standard song song so với 1 lượt deep:**
  - Song song không lợi: C cho cùng số mục mới với B2 nhưng đắt gấp 3, B2 thắng 6/6 seed.
  - Deep không hơn standard: B so với B2 là 2,4 so với 2,0 mục mỗi lượt, không khác có ý nghĩa, mà đắt gấp đôi.
  - Điểm mạnh thật của ô: giảm lặp mà không cần danh sách (ρ 0,20 so với 0,76), tuân thủ năm 100% ở Q&A, trích dẫn hợp lệ 0,95.
- **Câu 7, xếp hạng:**
  - Chắc chắn nhất: **B tốt hơn A rõ rệt** (thắng 6/6 seed).
  - Theo phần đóng góp: luật cứng nâng tỷ lệ hợp lệ từ 50% lên 96% nhưng không giảm lặp; danh sách ≤50 mục mới là thứ giảm lặp (ρ từ 0,76 xuống 0,14).
  - D chậm (118 s, phần lớn do bước trích khoá bằng LLM) và 4/12 lượt ra 0 mục.
- **Kiểm tay 10 mục mới ngẫu nhiên:**
  - 8 mục trông thật, trong đó 3 mục chỉ liên quan yếu tới câu hỏi;
  - 1 mục sai danh tính issue;
  - 1 mục không kiểm được.

## Khuyến nghị cho production
1. Code ghép prompt sau planner: **luật cứng đặt đầu** (bỏ câu "≥3 series" vì model hiểu thành hạn ngạch và dừng đúng ở 3 mục; kiểm đa dạng series bằng code) + **danh sách tránh ≤50 dòng** + **bỏ digest 9k**.
2. **1 lượt `standard`**, không dùng `deep`; whitelist thêm **comicbook.com và screenrant.com**.
3. Lọc cứng bằng code (luật, khoá issue so với sổ, gắn lại URL; khoảng 20–25% mục bị loại vì URL).
4. Thiếu mục thì **chạy thêm 1 lượt standard cùng prompt**: hai lượt độc lập cho mục khác nhau ~79%, nên +$0.05 được thêm khoảng 2–3 mục. Chỉ chia ô (C+) khi danh sách đã bão hoà.
5. Chưa dùng chạy song song mặc định, chưa dùng `deep`, chưa dùng Search-first.

## Giới hạn
- n rất nhỏ (6 seed, 1–2 lượt lặp, 0–6 mục mỗi lượt).
- Tập "đã làm" chỉ 5–33 mục, nhỏ hơn sổ thật sau này.
- Năm và series do model tự khai; chỉ kiểm URL và snippet.
- Định dạng micro phân loại bằng LLM, kém tin cậy với comic 2026.
- Cấu hình nhánh D mới thử một lần, chưa tối ưu.
- Yêu cầu "tối đa 15 mục" không làm tăng số mục: số mục bị giới hạn bởi số nguồn truy xuất (5–8 mỗi lượt).

## Nên đo tiếp
- ≥20 seed × ≥3 lượt cho B2, X, "X + 1 lượt bù" và C+.
- Sổ "đã làm" 50–200 mục, để đo tác động của độ dài danh sách.
- Kiểm sự thật của ≥50 mục mới.
- Mô phỏng gắn lại URL trên 240 response đã lưu (không tốn tiền).
- Đo năng suất theo từng ô năm × hình thức.
