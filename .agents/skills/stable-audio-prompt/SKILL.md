---
name: stable-audio-prompt
description: Tạo prompt và thiết lập nhạc nền không lời (instrumental, no vocals) chuẩn xác cho Stable Audio 3 trên Hugging Face, tự động quét dự án video mới nhất và khớp độ dài (duration), phong cách và tiết tấu (BPM/Key) với video đã render. Dùng khi Master muốn prompt nhạc cho video hoặc gõ /stable-audio-prompt.
---

# Stable Audio 3 Prompt Generator Skill

Skill này tự động phân tích video/audio của dự án comic mới nhất (được render ở Stage 5/7 hoặc có file `final.mp4` / `audio.wav`), lấy chính xác độ dài (duration in seconds), phong cách kịch bản, tâm trạng (mood), tiết tấu (BPM/Key), sau đó tạo ra prompt và negative prompt chuẩn chỉnh nhất cho mô hình **Stable Audio 3** trên Hugging Face.

## 1. Cách kích hoạt nhanh (Script tự động)

Chạy script tự động có sẵn trong skill:
```bash
python3 .agents/skills/stable-audio-prompt/scripts/get_music_prompt.py
```
Hoặc chỉ định cụ thể một project:
```bash
python3 .agents/skills/stable-audio-prompt/scripts/get_music_prompt.py --project <tên_project>
```

Script sẽ tự động:
1. Quét tìm project mới nhất có video (`final.mp4` hoặc `audio.wav`) trên cả Mac cục bộ và máy Windows (`winbox-lan`).
2. Đo độ dài chính xác bằng giây để khớp thời lượng nhạc với video.
3. Đọc `music.json`, `narration.json` và `comic_context.json` để trích xuất:
   - Thể loại âm nhạc (`genre`: minimal dark cinematic, dark trap, hybrid orchestral...)
   - Nhịp tim / tempo (`BPM`: ví dụ 68 BPM)
   - Tông nhạc (`Key`: ví dụ C minor)
   - Nhạc cụ chính (`instruments`: cello, felt piano, sub-bass, 808...)
   - Màu sắc cảm xúc (`mood`: cold tension, introspective, psychological...)
4. Xuất ra cấu trúc prompt tối ưu cho Stable Audio 3 để Master dán ngay vào Hugging Face.

---

## 2. Quy chuẩn Prompt Engineering cho Stable Audio 3 (No Vocals)

Stable Audio 3 là mô hình sinh âm thanh khuếch tán (latent diffusion) chất lượng cao nhất hiện nay của Stability AI. Để tạo nhạc nền không lời (instrumental) khớp hoàn hảo với video, prompt phải tuân theo cấu trúc sau:

### Cấu trúc Positive Prompt chuẩn:
```text
[Genre / Sub-genre], [Lead & Rhythm Instruments], [Atmospheric & Spatial FX], [Mood / Emotional Arc], [BPM], [Key / Scale], [Mastering / Production Quality], instrumental, no vocals, no singing, no speech
```
* **Bắt buộc có:** Cụm từ khóa `instrumental, no vocals, no singing, no speech` ở cuối prompt.
* **Quy tắc nhạc cụ:** Liệt kê rõ texture (âm sắc) của từng nhạc cụ thay vì chỉ ghi tên chung (ví dụ: `solo cello with deep mournful tone`, `felt upright piano in low register`, `warm analog sub-bass`).
* **Tránh từ ngữ gợi ý giọng hát:** Tuyệt đối không dùng các từ như `soulful`, `choir`, `chanting`, `diva`, `ballad` vì model dễ tự ý chèn giọng người.

### Cấu trúc Negative Prompt chuẩn:
```text
vocals, singing, human voice, speech, spoken word, choir, vocal chops, acapella, talking, whispering, distortion, clipping, muffled, low quality, noise, harsh frequencies
```

---

## 3. Các thông số cài đặt trên Hugging Face

Khi dán vào Hugging Face Space ([stabilityai/stable-audio-3](https://huggingface.co/spaces/stabilityai/stable-audio-3)):

| Tham số | Giá trị khuyến nghị | Giải thích |
| :--- | :--- | :--- |
| **Duration (Seconds)** | Khớp chính xác với video (kéo thanh trượt đến số giây của `final.mp4`) | Khớp 1:1 với độ dài video Short/Longform |
| **Steps** | `8` (hoặc `12` nếu muốn mịn hơn) | Stable Audio 3 tối ưu ở 8 steps (inference nhanh ~5s) |
| **CFG Scale** | `1.0` – `7.0` | `1.0` là chuẩn của không gian HF; chỉnh lên `7.0` nếu muốn model bám chặt chẽ 100% vào prompt |
| **Seed** | `-1` | Ngẫu nhiên để thử nghiệm nhiều biến thể khác nhau |
