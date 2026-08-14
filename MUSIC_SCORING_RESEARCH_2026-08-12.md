# Nhạc nền cho pipeline — research + số đo thật
*2026-08-12 · 2 vòng deep-research (107 + 102 agent) + 3 probe đo trực tiếp trên `cap-shield-broken`. Mọi con số dưới đây hoặc là trích primary source, hoặc là do tôi đo trên máy này — không có con số nào chép từ blog.*

**Trạng thái:** research xong, kế hoạch đã duyệt, đang implement. Đọc file này TRƯỚC khi research lại bất cứ mục nào.

---

## 0. Vì sao video chưa có nhạc — không phải thiếu file

Đường nhạc trong comic pipeline **đã bị gỡ khỏi code**, `.env` vẫn trỏ vào chỗ trống:

| chỗ | tình trạng |
|---|---|
| `stages/stage_5/audio.py:17` | `mix_audio(tts_wav, out_path, *, progress)` — chỉ loudnorm, **không có tham số nhạc** |
| `stages/stage_5/pipeline.py:206` | gọi đúng 2 tham số → **không đường nào cho nhạc lọt vào** |
| `.env:59` | `BG_MUSIC_PATH=assets/bgm/default.mp3` — file không có, mà cũng chẳng ai đọc |
| `assets/bgm/README.md` | vẫn hứa *"0.35 gain with sidechain ducking"* — mô tả code đã bị xoá |
| `art_pipeline/assemble.py:24` | import `_resolve_bgm` **không còn tồn tại** → `ImportError`, module không import nổi |
| `art_pipeline/assemble.py:588` | gọi `mix_audio(audio, bgm, mixed, ...)` — 3 tham số vào hàm nhận 2 |

→ **Bước assemble của art pipeline đang chết**, chưa ai phát hiện vì test art nằm ngoài lệnh test chuẩn.

---

## 1. Nguồn nhạc — không nguồn "royalty-free" nào an toàn

Ràng buộc cứng: Short bị Content ID claim = mất phân phối.

### Đã verify (3-0 / 2-1)

**Pixabay — KHÔNG zero-claim.** Chính Pixabay viết: *"Many music composers, including those that share their work on Pixabay, have their content digitally fingerprinted via Content ID... They may also use a third-party platform to administer and manage their content."* Họ xây shield-icon + toggle khai báo Content ID + download certificate để dispute. Rủi ro **per-track nên lọc được**, nhưng nhãn do contributor tự khai → track không nhãn **không phải bảo đảm**.
`https://pixabay.com/blog/posts/how-to-clear-a-youtube-content-id-claim-with-a-pix-190/` · `https://pixabay.com/service/faq/`

**Epidemic Sound — claim-by-default.** Cả catalog nằm trong Content ID; trả tiền **không** tự miễn claim, phải safelist từng channel **trước khi** publish. Shorts vẫn bị claim kể cả đã safelist đúng. ES tự thừa nhận *"platforms and claimants control the release of claims"* — họ không gỡ được claim hộ mình.
⚠️ Trang **marketing** của ES hứa ngược lại — luôn trích help-center, đừng trích trang bán hàng.

**Policy của Google.** Tài liệu Content ID gọi tên nguyên nhóm *"so-called 'royalty free' production music libraries"* là loại phải qua **manual review trước khi claim**.
⚠️ **Chỗ cực dễ trích sai:** cổng chặn nằm ở bước **claim**, KHÔNG nằm ở bước nạp reference. Audio **vẫn bị fingerprint**. Đừng đọc thành "thư viện đó an toàn".
`https://support.google.com/youtube/answer/2605065`

### AI music

**MusicGen / AudioCraft — KHÔNG dùng được cho kênh monetize.** Code MIT nhưng **weights là CC-BY-NC 4.0**. Rủi ro phát sinh ngay lúc **chạy checkpoint**, không phụ thuộc ai sở hữu file WAV ra.
`https://github.com/facebookresearch/audiocraft`

**Stable Audio Open — dùng được, có trần cứng.** Stability AI Community License **tự động chấm dứt** khi bạn/affiliate vượt **1.000.000 USD doanh thu/năm** — tính tổng mọi nguồn, *"regardless of whether that revenue is generated directly or indirectly from the Stability AI Materials"*. Không notice, không cure period. Nhạc đã sinh vẫn giữ; máy sinh nhạc dừng.
⚠️ Cấu trúc cấp phép thương mại chính xác của bản 1.0 **chưa verify sạch** (một claim về nó bị bác 0-3). Đọc lại `LICENSE.md` + `stability.ai/community-license` trước khi ship production.

**Stable Audio Open Small** — 341M params, chạy **hoàn toàn trên CPU**, tối đa **11 giây**/lần. Training data 486.492 bản ghi CC0/CC-BY, đã sàng bản quyền bằng Audible Magic → củng cố luận điểm un-claimable.
⚠️ Số đo là trên **Arm/mobile**, không phải x86; repo public **không ship sẵn đường inference CPU**; model **mạnh về sound-effect/texture hơn là music**.

**Suno / Udio / Riffusion / ElevenLabs Music: TRỐNG.** Không một claim nào sống sót về điều khoản thương mại, về việc vendor có tự fingerprint output không, hay hệ quả kiện tụng RIAA. Chưa research được — đừng ai trích như đã biết.

### Bản quyền

USCO (báo cáo chính thức 01/2025): nhạc AI thuần **không được bảo hộ**; *"No matter how many times a prompt is revised and resubmitted"* — prompt không tạo authorship. Nhưng **selection/coordination/arrangement** của con người trên vật liệu AI **được bảo hộ dạng compilation** (án lệ Zarya of the Dawn).

⚠️ **Bẫy:** không-có-bản-quyền **≠** miễn nhiễm Content ID. Content ID là hệ thống fingerprint **tư nhân theo hợp đồng**, không phải phán quyết bản quyền.

### Kết luận nguồn

**PRIMARY = tự sinh tại chỗ.** Chỉ audio **chưa từng tồn tại ở đâu** mới thoả "zero claim" theo nghĩa đen — không có gì trong reference DB để khớp.
**FALLBACK =** Pixabay đã **lọc sạch** mọi track có shield-icon ở bước ingest, kèm log per-track.
**LOẠI khỏi vai trò primary:** Epidemic Sound, MusicGen.

---

## 2. Mức nhạc — không chuẩn nào cho con số

**Phát hiện trung tâm, và nó là phủ định:** KHÔNG standards body nào (W3C, EBU, ATSC, Netflix) quy định con số dB/LU cho mức nhạc so với lời. Cả bốn chỉ định nghĩa **quan hệ anchor** rồi giao balance cho tai người mix.

Bằng chứng đắt nhất — ATSC A/85, tải nguyên 72 trang rồi grep: **0 lần** xuất hiện từ gốc `intelligib`, 0 hit cho `below dialog`/`duck`. Annex I.6 *Content Loudness During Mixing*, đúng chỗ con số phải nằm nếu tồn tại, viết vỏn vẹn:

> *"With the monitor level set correctly, always mix relying on your hearing."*

→ **Bất kỳ ai nói "chuẩn X quy định nhạc thấp hơn lời Y dB" đều đang trích sai.**

### WCAG 20 dB — KHÔNG áp dụng được (11/12 claim bị bác đều vì thổi phồng điều này)

SC 1.4.7 / G56 có con số 20 dB, nhưng:
- **Level AAA**, scope normative giới hạn ở *"prerecorded audio-only content"*; glossary định nghĩa audio-only = *"a time-based presentation that contains only audio (**no video and no interaction**)"*. Video comic + TTS là synchronized media → **ngoài scope**.
- Đo bằng **dB(A) SPL** (âm học, A-weighted), không phải LUFS, không gating → **không ghép trực tiếp** vào chuỗi EBU R128.

Áp 20 dB là lựa chọn thẩm mỹ, **không phải tuân chuẩn**.

### Thứ duy nhất mang tính chuẩn: LDR

**EBU Tech 3343 V4 (Nov 2023):** `LDR = Programme Loudness − Dialogue Loudness ≤ 5 LU`. Scale-invariant → với program −14 LUFS, Dialogue Loudness không được thấp hơn **−19 LUFS**. Trạng thái: **advisory**, và scope gốc là cinematic content → dùng cho mình là mở rộng ngoài phạm vi tài liệu.

**Quy trình mix của Tech 3343** (advisory): đặt giọng dẫn **trước**, dùng cửa sổ **Short-term 3 giây** (bắc cầu qua khoảng lặng giữa từ/câu), cố ý đặt **hơi thấp hơn** target vì nền chỉ *cộng thêm* vào Programme Loudness. Không cho con số "thấp hơn bao nhiêu".

**ATSC A/85:** short-form (<~2-3 phút) phải đo **full mix**. Short 45-75s của mình rơi đúng vào đó — và `audio.py` đang làm đúng vậy.

**Netflix:** −27 LKFS dialog-gated là **bắt buộc**, nhưng đó là spec giao hàng của họ; **không** quy định hiệu số dialogue-to-music nào. Yêu cầu về nhạc của Netflix là **tách stem**.

⚠️ **−14 LUFS của YouTube CHƯA verify** từ nguồn primary của YouTube trong cả 2 vòng — nó là giả định đầu vào, mà `audio.py:12` đang xây trên nó.

---

## 3. SỐ ĐO THẬT trên máy này *(quan trọng hơn cả 2 vòng research)*

### 3.1 LDR ≤ 5 LU là ràng buộc lỏng — không dùng làm tiêu chí nghiệm thu được

Đo trên `projects/cap-shield-broken/audio.wav` (61.22s, −23.44 LUFS — khớp đúng log Stage 5). Bed = brown noise, `amix=normalize=0`, đo bằng đúng phương pháp production (`loudnorm print_format=json` → `input_i`):

| nhạc thấp hơn lời | mức nhạc | mix đo được | **LDR** |
|---|---|---|---|
| −5 LU | −28.44 LUFS | −22.45 | **+0.99 LU** |
| −10 LU | −33.44 | −23.16 | +0.28 LU |
| −15 LU | −38.44 | −23.33 | +0.11 LU |
| −20 LU | −43.44 | −23.40 | +0.04 LU |
| −25 LU | −48.44 | −23.43 | +0.01 LU |
| −30 LU | −53.44 | −23.44 | +0.00 LU |

Ngay cả khi nhạc chỉ thấp hơn lời **5 LU** (to đến mức không ai mix vậy), LDR mới đạt 0.99 LU — chưa tới 1/5 ngưỡng. **Ràng buộc này không bao giờ kích hoạt.** Giữ như lưới an toàn, đừng kỳ vọng nó bắt mix dở.

**Hệ quả quan trọng:** chỉnh nhạc từ −15 xuống −25 LU chỉ xê dịch loudness cuối **0.07 LU** — dưới sai số đo. → **Mức nhạc và tuân thủ −14 LUFS là hai núm ĐỘC LẬP.** Chỉnh bằng tai thoải mái, không thể làm vỡ chuỗi loudnorm.

**Câu hỏi gate BS.1770:** không có vách gate. Nhạc đóng góp giảm dần trơn tru theo phép cộng công suất (lý thuyết −20 LU → +0.043 dB; đo +0.04). **Không được giả định nhạc vô hình với bước normalize.**

### 3.2 `word_timestamps.json` là số NỘI SUY — không dùng để duck

```
171 words → 1 speech region (không khoảng nghỉ nào ≥ 0.35s)
gap distribution: {0.0: 170}          ← CẢ 170 khoảng cách đều đúng 0.0
'Captain' 0.0→0.4307826  'America' 0.4307826→0.8615652  'carries' 0.8615652→1.2923478
```

Mọi từ dài y hệt nhau. Đúng như `chatterbox_tts.py` tự mô tả: *"spreads each sentence's words inside its OWN measured duration"*. **Duck theo nó sẽ chìm 61 giây rồi không bao giờ nhả.**

Audio thật **có** khoảng nghỉ — `silencedetect=noise=-35dB:d=0.25` tìm ra 0.26–0.75s, đều đặn ~3-5 giây/lần (ranh giới câu).

→ **Nguồn dữ liệu đúng là `silencedetect` trên audio thật.** Vẫn deterministic (pass phân tích offline).
*(Kèm theo: word timestamps chỉ chính xác ở mức CÂU, không mức TỪ.)*

### 3.3 Tham số ffmpeg — lấy từ chính binary sẽ render

`ffmpeg -h filter=sidechaincompress` (ffmpeg 8.1):

| | |
|---|---|
| `threshold` | **LINEAR 0.000977–1**, default `0.125` — *không phải dB*. 0.125 ≈ **−18.06 dBFS** |
| `makeup` | cũng linear (1–64) |
| `detection` | default **rms** |
| `attack`/`release` | ms, default **20 / 250** |
| timeline `enable` | `volume` + `sidechaingate` **CÓ**; `sidechaincompress`/`acompressor` **KHÔNG** |
| `volume.eval` | default **`once`** — muốn gain biến thiên **BẮT BUỘC** `eval=frame`, quên là biểu thức bị thu về hằng số (bẫy im lặng) |

**Duck bằng automation đã test:** `volume` + biểu thức + `eval=frame` render sạch, đo lại đúng **−9.00 dB** dưới lời, khớp tuyệt đối đường cong viết ra. Biểu thức 188 ký tự.

### 3.4 Bộ phân loại action hiện tại không đủ để lái nhạc

`shots.py:3849 _ACTION_WORDS` là danh sách phẳng ~60 động từ đấm đá. Chạy trên chính narration này:

```
  .     s3:0  "The dark god casually catches the flying disc mid-air"   ← BỎ SÓT
  .     s5:0  "Gath casually snatches the heavy weapon out of the sky"  ← BỎ SÓT
  .     s5:1  "and literally BITES a huge chunk out of the metal"       ← BỎ SÓT (payoff đáng nhớ nhất video)
ACTION  s6:1  "and had seized control of the country"                   ← BÁO NHẦM (kể lể chính trị)
ACTION  1:1   "but three people completely destroyed the metal"         ← BÁO NHẦM (câu thesis)
```

### 3.5 LLM cue map — đã test 4 lần với `deepseek/deepseek-v4-flash`

**Xương sống ổn định tuyệt đối (4/4 lần giống hệt):**

| beat | role | 4 lần |
|---|---|---|
| `3:1` bóp vỡ khiên | impact | **5·5·5·5** |
| `5:1` cắn miếng khiên | impact | **5·5·5·5** |
| `7:1` Miles đấm vỡ | impact | **5·5·5·5** |
| `3:0`,`5:0` chộp/bắt đĩa | riser | **4·4·4·4** |
| `2:1`,`4:1` ném khiên | build | **3·3·3·3** |
| outro | landing | **2·2·2·2** |

Nó sửa đúng cả 5 lỗi ở 3.4, và phân biệt được **wind-up với payoff** (bắt đĩa = riser, bóp vỡ = impact). Hai câu meme được gán `role: aside` **4/4 lần** → thả nhạc xuống nhường câu đùa.

**Ba defect ép ra 3 guard bắt buộc:**
1. **1/6 lần trả về RỖNG** — đúng cảnh báo `config.py:68` *"DeepSeek V4 Flash (returns empty content under load)"* → **bắt buộc model chain + retry**
2. **Aside dao động 3/1/1/2** nhưng `role` thì aside 4/4 → **LLM trả `role`, CODE suy ra `intensity`**
3. **Accent dùng 9/5/5/8 với trần khai báo là 4** — vượt trần **4/4 lần** → **cắt bằng code, không tin model tự đếm**

10/17 beat trùng khít tuyệt đối; dao động lớn nhất là 2 và chỉ ở beat năng lượng thấp.

---

## 4. Prior art — phương pháp này đã có tên ở 2 ngành

**Phần LLM = "spotting session" của điện ảnh.** Đạo diễn + nhạc sĩ xem bản dựng khoá, quyết định nhạc vào/ra chỗ nào, sắc thái gì; kết quả là **spotting sheet** (timecode từng cue). Tài liệu nghề nhấn: *đôi khi quan trọng không kém là xác định chỗ nào KHÔNG đặt nhạc* — đúng cái `role: aside` làm.
→ `music_direction.json` **chính là spotting sheet tự động**.

**Phần stem = "vertical layering" (vertical remixing) của game audio.** Xuất nhạc thành stem chạy song song, bật/tắt theo cường độ. Dùng trong Red Dead Redemption, Pokémon Sword/Shield; middleware chuẩn FMOD/Wwise. Phân biệt với *horizontal re-sequencing* (điều khiển cấu trúc thay vì cường độ).

**Phần ghép đã được publish và đo: JenBridge** — Jen Music AI, 01/06/2026, `arXiv 2606.01703`.
> LLM Agent (Qwen3-8B) đóng vai **creative director**, quyết định theo từng cặp clip nên dùng kiểu chuyển nào (generative / cắt phựt / khoảng lặng / fade).

Benchmark 120 video, 567 scene: **Transition Naturalness 4.2/5** (hơn baseline tốt nhất 0.9 điểm), ImageBind alignment +38%.

**Ba khác biệt — theo hướng có lợi cho mình:**

| | JenBridge | Cách mình |
|---|---|---|
| nhạc | sinh full track mỗi đoạn | **sắp xếp stem cố định** |
| chia đoạn | PySceneDetect — **dò từ pixel** | **đã biết sẵn** từ Stage 3 + review |
| render lại | ra khác | **ra y hệt** |

Điểm 2 là lợi thế thật: họ phải *đoán* ranh giới cảnh, mình *có sẵn* ranh giới do Master duyệt.
**Điểm đáng học:** JenBridge chi nhiều công cho **chuyển tiếp giữa đoạn** và đo được đó là chỗ ăn điểm nhất → chỗ nhạc đổi lớp là chỗ dễ lộ nhất, phải để tâm.

**Công cụ thương mại** (Beatoven.ai, Soundraw, Mubert, AIVA) làm bản yếu hơn: một mood cho cả track, không chấm từng beat — và đều là dịch vụ bên thứ ba → quay lại bài toán license ở mục 1.

---

## 5. Kiến trúc đã chốt

**ARRANGE stem, đừng GENERATE bài mỗi video.** Lý do:
1. Stage 5 bị re-render liên tục → sinh mỗi lần thì không tái lập được
2. Máy CPU-only (AMD 780M, không CUDA) → sinh nhạc runtime là chờ hàng phút
3. QC một lần cho bộ stem, không phải mỗi video
4. **Bản sắc kênh** — `config.py:196-201` đã ghim MỘT giọng đọc sau khi 5 video ship 3 narrator khác nhau (*"a channel's voice is its strongest recognition asset"*). Mỗi video một bài AI khác nhau là tái lập đúng lỗi đó.

Trần 11 giây và "mạnh texture hơn music" của model **khớp đúng** thứ stem cần — nhược điểm của nó là ưu điểm ở đây.

**Cache CHỈ DẪN, không cache BẢN PHỐI.** `music_direction.json` nhỏ, ghim hash, sửa tay được; audio thì dựng lại mỗi render. Vừa deterministic vừa không thể ôi.
*(Liên quan: `stage_5/pipeline.py:203` cache `audio_mixed.wav` bằng "file có tồn tại không" — `stage_4 --force` rồi `stage_5` không force sẽ xài lại mix cũ. Hiện vô hại vì mix chỉ là loudnorm; có nhạc rồi thì nó giữ cả bản phối lỗi thời.)*

---

## 6. Còn nợ

- **Cách soạn stem**: loop point, tail handling, độ dài crossfade, nhất quán tempo/key, quantize transition theo bar — 2 vòng research đều chưa lấy được. Chỉ ảnh hưởng *nhạc nghe hay tới đâu*, không chặn đường ống.
- **Bằng chứng đo được** về bed liên tục vs vào/ra theo beat, và về im lặng trước câu chốt: chưa có. Bằng chứng duy nhất sống sót lại **ngược hướng** (Moreno & Mayer 2000: nhạc dưới narration giảm retention ~33%) — nhưng là *learning retention* qua headphone, 26 năm trước, không LUFS/không ducking, và nhóm **chỉ-nhạc không khác biệt có ý nghĩa** với nhóm chỉ-narration. **Không được** dùng nó để kết luận mix khéo là vô ích (claim đó đã bị bác 0-3).
- **Suno/Udio/ElevenLabs/Riffusion**: trống hoàn toàn.
- **Artlist, Uppbeat, Bensound, FMA, YouTube Audio Library**: không claim nào sống sót — **không suy ra được** là an toàn hay không.
- Câu *"claim lên CC0 là vi phạm policy nên dispute được"* đã **bị bác 0-3** — không dùng lập luận này.

**Thời hạn:** trang vendor là chính sách sống. Mọi kết luận gắn mốc **2026-08-12**, nên re-verify định kỳ.
