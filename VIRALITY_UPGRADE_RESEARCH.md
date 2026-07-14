# Viral Upgrade Research v2 — Grimframe comic Shorts → 10k+ views

> **2026-07-08 · v2 (VERIFIED)** — thay bản v1 sau khi Master nghi ngờ số liệu. Mọi claim v1 được adversarial-verify (workflow 3-phiếu, nguồn official) + 2 Sonnet triangulate/competitor pass. **Mỗi số ở đây mang nhãn confidence.** Nghi ngờ của Master ĐÚNG: phần lớn con số v1 là blog-farm chép vòng tròn, không truy được nguồn gốc.
> RESEARCH-ONLY — chưa sửa code. Audit code (lever ↔ file:line) đọc code thật nên giữ nguyên giá trị.

**Nhãn:** ✅ OFFICIAL (YouTube chính chủ) · 🟡 REAL-STUDY (nghiên cứu thật, có tên + methodology — Paddy Galloway 2023, 5.400 Shorts/3.3B views; data TRƯỚC đổi cách đếm view 3/2025, ngoại suy sang 2026) · 👁 OBSERVED (tự fetch thấy) · ❌ DEBUNKED (không truy được nguồn — coi như bịa).

---

## 0. Cái gì THẬT, cái gì BỊA (đối chiếu v1)

| Claim v1 | Verdict |
|---|---|
| "Retention ≥65% (<30s) / ≥50% (30-60s) để được đẩy" | ❌ DEBUNKED — blog-farm verbatim, trang bị gán nguồn (vidiq) không hề chứa số này |
| "Seed pool 50-500 viewers" | ❌ DEBUNKED — khái niệm seed THẬT (✅ product lead Todd Sherman), con số bịa |
| "Swipe-away >40% = chết seed" | 🟡 NỬA THẬT — Galloway 2023: VVSA <60% "rarely performed well"; framing "giết ở pha seed" là suy diễn blogger |
| "VVSA lành mạnh 70-80%" | 🟡 gần đúng — Galloway: best performers **70-90% VVSA** |
| "Replay = view từ 31/03/2025" | ✅ OFFICIAL — "every play and replay as a view, no minimum watch time"; "engaged views" (≥30s) giữ riêng cho YPP/monetization |
| "AVD >100% được boost mạnh" | ❌ mức độ boost không ai đo; cơ chế toán học (rewatch) đúng |
| "Sweet spot 30-45s" | ❌ không nguồn. "50-60s tốt nhất" = 🟡 Galloway 2023 (nghiên cứu thật duy nhất về độ dài, đã cũ, bị nhiều blog misattribute thành "Inflow Network") |
| "Ranking dùng ngưỡng cụ thể" | ✅ OFFICIAL ngược lại: YouTube chỉ nêu 3 tín hiệu — **% viewers who chose to view (VVSA), avg view duration, avg % viewed** — KHÔNG công bố ngưỡng số nào |
| "Stall ~1000 view" | ✅ OFFICIAL cơ chế: seed-audience exploration — view đầu là thăm dò, compound hay tắt theo phản ứng seed (không có con số pool) |
| "Policy inauthentic 07/2025 nguy hiểm mới cho AI" | ✅ OFFICIAL đính chính: chỉ là RENAME "repetitious content" (chính sách cũ); **AI không tự động vi phạm** — cần original/authentic + disclose synthetic realistic. Ví dụ mass-produced của YouTube: "narrated stories only superficial differences", "slideshows same narration" (1 phiếu verify, coi low-confidence) |
| "Hook mấy giây đầu quan trọng, mọi độ dài" | ✅ OFFICIAL (product lead, đúng nguyên văn: 15s hay 60s đều vậy) + VVSA là metric thật trong Analytics |

---

## 1. Competitor (👁 observed + giới hạn thật)

- **Kênh tên tuổi KHÔNG phải đối thủ cùng format:** Comicstorian (đọc kịch tính, Shorts là phụ), Comics Explained / Variant / NerdSync / ComicPOP đều **có host người thật** — cơ chế giữ chân khác faceless. Đừng copy làm chuẩn. (@comicstoriantv = kênh podcast, đừng benchmark nhầm — ✅ verified.)
- **Đối thủ faceless đúng lane:** **Comics Unlocked** (@ComicsUnlocked) — tagline chính chủ *"Gimme 60 sec & I'll turn you into a comic expert!"*; ~650K subs / ~1.1B tổng views [BLOG claim, không tự verify được]. Comic Vault, Marvel Explained khớp mô tả nhưng không xác minh được số. → **Format faceless comic Shorts là thị trường thật đang scale, hướng Grimframe đúng.**
- **Title = hook chính của genre faceless** (không có host để hook bằng mặt/giọng). 3 khuôn title thật (👁 URLs thật):
  1. `"[Char] Does/Dies [hành động sốc]"` — hậu quả sốc trước, giải thích sau ("Homelander Dies When Butcher Does THIS").
  2. `"[Sự kiện] — and [nghịch lý]"` — ("Superman Threw Lobo Into the SUN — and He Still Came Back") = đúng shape "thought...until" đã tune.
  3. `"The [Franchise] That [outcome bí ẩn]"` — câu hỏi ẩn.
  → Register title-smith + hook hiện tại của kênh **đã đúng hướng, giữ**.
- Giới hạn: YouTube + Social Blade/vidIQ/Nox chặn fetch → không verify được view-count video cụ thể nào. Muốn số thật: YouTube Data API (có key) hoặc xem tay.

---

## 2. Lever còn đứng vững (đã lọc) ↔ code

**Tín hiệu ranking thật (✅): VVSA, avg view duration, avg % viewed.** Không có ngưỡng công bố — tối ưu HƯỚNG, đừng thờ con số.

| # | Lever (confidence) | Pipeline hiện tại | Chỗ sửa |
|---|---|---|---|
| 1 | **Loop/replay** — replay đếm là view, không cần watch-time tối thiểu (✅) | Outro card OFF ✓, loop-tease ON ✓ — ĐÚNG HƯỚNG. Nâng cấp còn lại: match frame đầu≈cuối / cắt trên chuyển động để giấu seam | `config.py:316,185`; stage_5 ending |
| 2 | **Hook giây đầu / VVSA** — quan trọng mọi độ dài (✅); best performers VVSA 70-90% (🟡) | Frame-1 defect (memory: chưa fix triệt để) = ROI render cao nhất; hook recap band bất nhất validator 7-18 vs rule 14-26; hook Q&A deterministic/flat | `shots.py:1286`; `write_script.py:424 vs 62-63`; `explore_answer.py:270` |
| 3 | **Title-as-hook** — 3 khuôn observed (👁) | title-smith register meme-flip đã khớp; vẫn là bước tay, không tự sinh trong pipeline | `.claude/agents/title-smith.md` |
| 4 | **Độ dài 50-60s** (🟡 Galloway 2023, nguồn thật duy nhất) | Recap ~61-76s (hơi trên band); **Q&A không có trần giây** — Juggernaut ra 35.6s (DƯỚI vùng tốt), 6-item có thể vọt 81s+ | `write_script.py:41-63`; `explore_answer.py:42-49` |
| 5 | **Avg % viewed** → không dead-middle, mỗi câu đẩy câu sau (✅ tín hiệu, không ngưỡng) | Body punchy 1-event/câu đã tune ✓; Task #1 (recap narration, Q&A vibe) phục vụ đúng lever này | `write_script.py:2028` |
| 6 | **Chống "mass-produced"** — ví dụ YouTube: same-narration slideshows (✅ policy, ví dụ low-conf) | Narration LLM mới mỗi video + archetype rotation + motion động = đang an toàn; AI được phép, cần original + disclosure synthetic realistic | toàn pipeline — giám sát, đừng để template cứng |
| 7 | Render polish — KHÔNG có số viral verified, chỉ best-practice (❌ số "caption +12-15%" là blog): BGM thiếu file → loudnorm không chạy; ZOOM_AMPLITUDE=0.06 vs docstring 0.10; CAPTION_POP=false; banner burn mọi frame | Audit code thật, đáng sửa vì chất lượng cảm quan — đừng kỳ vọng con số cụ thể | `assets/bgm/` + `audio.py:30`; `shots.py:148`; `config.py:302,346` |

---

## 3. Đề xuất update (rank lại theo bằng chứng, chờ Master duyệt từng mục)

**Tier A — bằng chứng mạnh (✅/🟡):**
1. **Seamless loop hoàn chỉnh** (frame cuối nối frame đầu / cắt trên motion) — lever verified mạnh nhất (replay=view). Effort trung.
2. **Fix frame-1/cold-open triệt để** — VVSA là cửa sinh tử official. Effort trung.
3. **Q&A: thêm floor+trần độ dài** hướng ~50-60s (Juggernaut 35.6s là ngắn so với vùng tốt của nghiên cứu thật duy nhất). Effort thấp. *(Lưu ý: data 2023 — đáng thử A-B, không thờ số.)*
4. **Thống nhất hook band recap + hook Q&A qua LLM** — phục vụ VVSA. Effort thấp-trung.

**Tier B — best-practice, không số verified:**
5. BGM default.mp3 + loudnorm narration-only path (audio đúng chuẩn loudness). Effort thấp.
6. ZOOM_AMPLITUDE 0.06→0.10 (khớp docstring) + CAPTION_POP thử A-B. Effort thấp.
7. `TITLE_BANNER_HOOK_ONLY=true` thử A-B (trả không gian dọc). Effort thấp.

**Bỏ khỏi kế hoạch (căn cứ bịa):** mọi mục tiêu "đạt 65%/50% retention", "seed 50-500", "sweet spot 30-45s", "caption +12-15%".

**Đang đúng — giữ:** outro OFF + loop-tease, narration gốc + RMS + atempo 1.35, title meme-flip, archetype rotation, MIRROR_PANELS=false, never-static motion, punchy 1-event/câu.

---

## 4. Nguồn chính
- ✅ support.google.com/youtube/answer/11914225 (3 tín hiệu ranking, không ngưỡng) · Creator Insider/Todd Sherman (VVSA metric + seed exploration + hook mọi độ dài) · support.google.com thread 356734251 (inauthentic = rename, AI ok) · youtube.com/shorts/1O9mukL8n_k + TeamYouTube (replay=view 31/03/2025).
- 🟡 Paddy Galloway & Chris Gileta, X 4/2023 (x.com/PaddyG96/status/1646898356419981315): 5.400 Shorts/33 kênh/3.3B views — VVSA 70-90% top, <60% yếu, 50-60s tốt.
- 👁 Comics Explained Shorts URLs (title formulas), @ComicsUnlocked tagline, @comicstoriantv mis-benchmark.
- ❌ Debunked farm: socialync, miraflow, humbleandbrag, retensis, dataslayer, nexora, virvid, shortimize... (chép vòng tròn không citation).
