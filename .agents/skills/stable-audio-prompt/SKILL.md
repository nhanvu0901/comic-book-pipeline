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

### Cấu trúc Positive Prompt chuẩn (No Intro — Instant Hook):

#### Phong cách 1: Galactus Signature Style (Dark Aggressive Drift Phonk — 140 BPM)
Dành cho video hành động, đấu trí, sức mạnh áp đảo (như video Galactus hay John Stewart):
```text
A dark aggressive drift phonk instrumental at 140 BPM. Instant start directly into the main hook from second 0, zero intro, no buildup, no ambient fade-in. Menacing detuned cowbell lead hook riff and heavy distorted 808 sub bass glides hitting immediately on the first beat. Fast rolling trap hi-hats, punchy kick drums, and dark cosmic synth stabs driving non-stop high energy throughout. Relentless continuous hook loop, pure aggressive momentum. Epic, cinematic, triumphant, strictly instrumental, no vocals, high production quality, clean punchy mix, wide stereo image.
```
*(Bản rút gọn - Short & Punchy):*
```text
Instant drop dark cosmic drift phonk instrumental, 140 BPM. Zero intro, straight into the main hook from 0:00. Aggressive distorted 808 bass, punchy trap drums, fast hi-hats, iconic detuned cowbell hook riff playing immediately, maximum energy, no buildup, purely instrumental, no voices, polished studio production.
```

#### Phong cách 2: Minimal Dark Cinematic (Brooding Cello & Piano — 68 BPM)
Dành cho video tâm lý, suy ngẫm sâu, không khí u tối:
```text
Minimal Dark Cinematic score, 68 BPM, C minor. Instant start with the full main theme from second 0, zero intro, no slow buildup. Deep atmospheric cello lead melody and felt upright piano chords playing immediately on beat one. Warm sub-bass pulse, continuous subtle cinematic percussion, cold introspective tension, brooding psychological atmosphere, cavernous wide soundstage, heavy analog saturation, long dark reverbs, film score soundtrack, dynamic range master, instrumental, no vocals, no singing, no speech.
```

### Cấu trúc Negative Prompt chuẩn:
```text
intro, buildup, slow start, gradual rise, ambient intro, silence at beginning, fade-in, prelude, vocals, voice, singing, spoken words, speech, humming, choir, muddy low-end, muffled mix, noisy distortion, low fidelity, out of tune
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
