# Micro-Moment v2 — spec copy-match đối thủ
*2026-07-10 · Fable tổng hợp từ 2 forensics đo thật (transcript 6 video + ffmpeg scene-detect 4 video tải về, so với batman-court-owls-7 nhà mình). Ràng buộc cứng: mọi thay đổi CHỈ chạm micro_moment (writer trong micro_moment.py; render qua env-knob default = hành vi cũ) — recap/Q&A byte-identical.*

## Chẩn đoán vì sao bản v1 "really bad"
| Đo | Nhà mình (Batman #7) | 4 video thắng (32k-2M views) |
|---|---|---|
| Cut/giây | **0.08** (3 cắt/36s, có đoạn đứng hình 2.9s) | **0.24-0.38**, không video nào có freeze |
| Shot trung bình | ~9s | 2.5-3.8s (Cosmo 6.7s nhưng zoom liên tục) |
| Panel chiếm khung | **35-45%** (contain+blur, nền mờ chết) | Full-bleed sát mặt/hành động |
| Chuyển cảnh | pan/dissolve mờ | **Cắt cứng 0-frame** (verify pixel) |
| Trang mở đầu | chiếm >50% runtime (0-13s + loop-tail 30-36s) | ≤3-4s rồi đi |
| Caption on-screen | TẮT (lệnh tắt để render nhanh) | **Cả 4 đều có** — chữ bật-nhảy 1-2 từ/câu highlight, chính là "năng lượng" thị giác |
| Hook | statement nghịch lý đứng riêng | **mirror của title**, tên nhân vật ngay câu 1 |
| Câu văn | 1-event/câu tách rời | **chuỗi dài and/but/then**, 2-4 sự kiện/câu, cuốn không ngắt |
| Dialog | luôn paraphrase | **quote nguyên văn** câu chốt/twist (2 video twist mạnh nhất) |
| Ending | landing-meaning cố định | 3 biến thể: thesis 1 câu · hard-cut ngay payoff · câu hỏi mở câu comment |
| wpm | ~200-227 | 183-225 — KHÔNG khác, tốc độ không phải vấn đề |

Phát hiện chiến lược: video 2M "Punisher Makes Juggernaut Throw Up" thực chất là **recap nguyên one-shot đội title-moment** (cái moment nằm ở 48% video). Mode 1-cảnh thuần có trần tự nhiên 34-61s.

## Spec v2

### A. Narration (sửa `_MICRO_WRITE_SYSTEM` + validator trong micro_moment.py — file riêng của mode)
1. **Scope**: từ "1 cảnh ±1 beat" → **chuỗi nhân-quả gọn quanh moment trong CÙNG issue** (mini-arc: dẫn vào → moment → hệ quả), moment đặt ở 40-70% video, KHÔNG để dành làm frame cuối. 35-60s (~120-200w).
2. **Hook = mirror của title**: statement lặp/diễn giải trực tiếp title, TÊN nhân vật trong câu 1. Bỏ yêu cầu nghịch lý-đứng-riêng.
3. **Câu chuỗi**: mỗi câu được nối 2-3 sự kiện bằng and/but/then/after/while (paratactic), thì hiện tại, ngôi 3, giọng documentary — vẫn từ vựng B2, không hype-slang. Đổi thì quá khứ→hiện tại đúng tại beat cảm xúc chính.
4. **Quote nguyên văn** câu thoại chốt từ panel dialog/OCR khi có (writer nhận dialog trong beat data) — không paraphrase câu mic-drop.
5. **Ending 3 biến thể** (writer chọn theo loại moment, validator nhận cả 3): (a) thesis 1 câu; (b) hard-cut ngay payoff — không landing; (c) câu hỏi mở bait comment.
6. `visual_beats` giữ nguyên (đã ship) — mỗi mệnh đề chuỗi = 1 beat.

### B. Render (env-knob, default = hành vi cũ; pipeline micro set knob khi invoke)
7. **`SHOT_MAX_SECONDS`** (mới, default 0 = off): micro set ~3.5 — shot dài hơn bị time-split thành nhiều shot cùng panel khác crop/motion (không đứng hình).
8. **`PANEL_FIT_MODE`** (mới, default "contain" = cũ): micro set "fill" — landscape crop lấp khung dọc; ngoại lệ tự động: panel `_panel_has_critical_text` giữ contain+blur (rule chữ đọc được có sẵn).
9. **`XFADE_TRANSITION`**: micro set "cut" (cắt cứng 0-frame). Không thêm flash/rung — đối thủ không dùng.
10. **Loop-tail ngắn**: intro shot ≤3.5s (theo #7) — trang mở không được chiếm quá ~15% runtime; loop-close giữ nhưng clone shot cuối ~1.5-2s.
11. **Caption bật lại CHO MICRO** (chờ Master quyết): captions.ass karaoke từng-từ ĐÃ có sẵn — burn trong `_final_encode` đang bị comment theo lệnh cũ "tắt cho nhanh". Đối thủ 4/4 dùng chữ bật-nhảy làm nguồn năng lượng chính. Đề xuất: knob `BURN_CAPTIONS` per-render, micro bật, mode khác giữ tắt.

### C. Không đổi
Chọn moment (moment-scout gate VISUAL SPECTACLE), grounding beat-anchored, TTS/atempo/RMS, seamless loop concept, panel-match core, mọi default của recap/Q&A.

## Việc mở (Master quyết)
- (1) Duyệt A+B? (2) Caption: bật cho micro? (3) Batman #7 re-render theo v2 sau khi code xong làm video test.
