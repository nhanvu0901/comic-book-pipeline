# Competitor & Algorithm Audit — 2026-07-12 (Phase 1, chưa nhìn code)

Nguồn: (a) forensics 12 kênh bằng yt-dlp metadata thật (không ước lượng), (b) deep-research thuật toán Shorts 2025-2026 có gắn nhãn độ tin cậy. Bản nhận định này được viết TRƯỚC khi soi code pipeline, để không thiên vị về phía những gì mình đã build.

Dữ liệu đầy đủ: `scratchpad/competitor_forensics.md`, `scratchpad/shorts_algorithm_research.md` (+ raw JSON từng video trong `scratchpad/channels/`, `scratchpad/deepdive/`).

---

## 1. Bức tranh đối thủ (số thật, ngày đo 2026-07-12)

4/11 kênh đối thủ trong danh sách không dùng được: `@Comiczz`, `@MoeSchmo` (kênh rỗng, 0 video public), `@DanCoNew`, `@DanExclamation` (handle 404). `@ComicEscape` hóa ra là kênh anime 7 sub — loại khỏi benchmark.

| Kênh | Median views | Top video | %>10k | Cadence | Trạng thái |
|---|---:|---:|---:|---:|---|
| **grimframe (mình)** | **1,150** | **5,900** | **0%** | ~5.0/tuần | đang chạy |
| ComicCut-77 | 4,750 | 32,000 | 15% | ~2.6/tuần | đang chạy |
| VillainHubs | 2,900 | 26,000 | 7.5% | ~1.9/tuần | đang chạy |
| TripFallDown | 1,200 | 6,300 | 0% | ~1.2/tuần | đang chạy |
| SkarShorts | **101,500** | 692,000 | **90%** | ~6.4/tuần | đang chạy — **benchmark sống quan trọng nhất** |
| TheCosmoComics | 11,000 | 1,200,000 | 82.5% | — | **ngưng Shorts từ 2025-04** (view là di sản) |
| RealQuickComics | 41,500 | 7,000,000 | 100% | — | **ngưng Shorts từ 2024-04** (view là di sản) |

Ba công thức viral quan sát được (từ 21 video top của các kênh):

1. **"Part N" serialized recap** (RealQuickComics): recap 1 câu chuyện cắt thành nhiều phần 60s, "Iron Man Becomes Ice Age Man - Part 1" = 7,015,369 views, like/view ~7.5%. Công thức mạnh nhất từng thấy trong niche — nhưng kênh đã ngưng.
2. **Power-fantasy statement** (SkarShorts — kênh sống mạnh nhất): "Spider-Man 3099 Is Unstoppable" (692k), "Silver Surfer 2099 Is Too Powerful" (501k), "Cyclops 2099 Is Overpowered" (500k). Đều 59-60s, title là TUYÊN BỐ dứt khoát về sức mạnh, chuỗi nhân vật biến thể sản xuất hàng loạt (6.4 video/tuần), like/view ~6%.
3. **Q&A / explainer** ("This is how BATMAN TRAINS himself" 1.2M, "How Venom KILLED Knull" 337k, "The Tragic Reason Harley Quinn Finally Left The Joker" 32k): vẫn viral nhưng trần thấp hơn 2 công thức trên ở các kênh nhỏ (ComicCut chỉ 22-32k).

Pattern cứng xuyên suốt:
- **Mọi video >10k view đều dài 34-63 giây.** Kênh duy nhất làm video 114-164s (TripFallDown) có top thấp nhất nhóm (3-6k) dù cùng nhân vật A-tier.
- **100% video viral dùng nhân vật A-tier** (Batman, Spider-Man, Venom, Joker, Hulk, Knull...).
- **Title thắng là STATEMENT, không phải câu hỏi** — trong 10 video view cao nhất, chỉ 2 là dạng câu hỏi, và cả 2 thuộc kênh đã có sẵn khối lượng lớn (Cosmo).
- Emoji không quyết định: 4/6 kênh mạnh nhất gần như không dùng emoji.
- Like/view của nhóm thắng 6-10%; ComicCut/VillainHubs 1-3%; **Grimframe 0.7-1.7%** — thấp nhất.

## 2. Grimframe đứng đâu (không tô hồng)

- Median 1,150 — **thấp hơn tất cả 6 đối thủ có dữ liệu**, kể cả kênh yếu nhất còn sống (TripFallDown 1,200).
- Video tốt nhất (5,978 — "4 Marvel characters Ghost Rider's Penance Stare couldn't break 💀") chưa vượt median của ComicCut (4,750) và thua top-3 của mọi đối thủ.
- 0% video vượt 10k.
- **Điểm sáng thật sự: cadence ~5/tuần, thứ nhì toàn nhóm** (chỉ thua SkarShorts). Năng lực sản xuất KHÔNG phải vấn đề — đây chính là lợi thế nếu tìm ra đúng công thức, vì SkarShorts thắng nhờ cadence cao × công thức lặp lại được.
- Video 81s ("Galactus Made Gambit...") chỉ 1,830 view — khớp với pattern "dài = chết" ở trên.
- 2 video tốt nhất của kênh đều là dạng **đếm số + hằng-số-bị-phá** ("4 characters X couldn't break", "3 Times the Unstoppable Juggernaut Got Stopped") — khớp forensics nội bộ 2026-07-10. Lưu ý: điều này **mâu thuẫn một phần** với kết luận research 2026-07-10 ("cấm đếm") — dữ liệu mới cho thấy đếm số KHÔNG phải vấn đề khi lõi cảm xúc là "hằng số bị phá"; cái chết là title tả-thực không có hằng số nào bị đe dọa.

## 3. Thuật toán Shorts — cái gì thật, cái gì lore

| Kết luận | Độ tin cậy |
|---|---|
| Signal ranking = avg view duration + avg % viewed + like + post-watch survey; không có ngưỡng công bố | OFFICIAL |
| Feed là swipe → **CTR không phải ranking factor**; title dùng để match topic/seed-audience + search, không phải câu click | OFFICIAL |
| Từ 31/3/2025 mỗi loop = +1 view thô (tách khỏi Engaged View) | OFFICIAL |
| "Kẹt 1k" = **trượt seed-test** (swipe-away cao/retention thấp trong batch đầu), KHÔNG phải block chủ động | COMMUNITY nhưng khớp cơ chế chính thức |
| 50-60% swipe-away xảy ra trong 3 giây đầu | MEASURED |
| "The flattening" (từ 9/2025): video >30 ngày bị giảm view đột ngột, video mới không — đừng trông cậy re-test video cũ | MEASURED (Tubefilter 12/2025, 2 creator lớn độc lập) |
| 1 video flop không phạt cả kênh, nhưng video lệch ngách gieo sai audience → hại gián tiếp video sau | OFFICIAL + suy luận |
| Sweet spot kể chuyện 20-45s; completion % quan trọng hơn độ dài tuyệt đối | MEASURED (đồng thuận cao) |
| Ngưỡng cụ thể "50-500 viewer test, 65% retention" | LORE — các blog SEO copy nhau, không truy được nguồn gốc |
| "New channel boost" | LORE — các nguồn mâu thuẫn nhau, tin cậy thấp nhất |

Hệ quả quan trọng nhất cho mình: **"bị chặn ở 1k" không tồn tại như một cơ chế.** 1k view ≈ kích thước làn sóng seed đầu tiên; không vượt được nghĩa là video thua bài kiểm tra swipe-away/retention, và YouTube (sau "the flattening") gần như không quay lại cho cơ hội thứ hai. Trận đánh quyết định nằm ở 3 giây đầu và completion rate — hai thứ đo được trong YouTube Studio ("How many chose to view" + retention curve).

## 4. Nhận định không thiên vị (viết trước khi nhìn code)

**Cái mình đang làm ĐÚNG (dữ liệu xác nhận):**
1. Nhân vật A-tier làm chủ đề — 100% video viral khảo sát đều vậy. Chiến lược Q&A archetype hiện tại đúng hướng.
2. Cadence ~5/tuần — tài sản lớn nhất của kênh, đúng mô hình SkarShorts (công thức lặp × tần suất cao).
3. Loop mặc định — cơ chế hợp lý, giữ.
4. Ngách nhất quán (comic recap/Q&A) — tránh được lỗi gieo sai audience.

**Cái mình đang làm SAI hoặc CHƯA ĐỦ (theo dữ liệu, không theo cảm tính):**
1. **Duration vượt vùng thắng.** Band hiện tại 61-76s; mọi video >10k của đối thủ nằm 34-63s; video 81s của mình chết (1,830). Trần trên nên là ~60s, không phải 76s.
2. **Title register nghiêng về câu hỏi/listicle, trong khi dữ liệu nói STATEMENT thắng.** SkarShorts (kênh sống mạnh nhất) toàn "X Is Unstoppable/Too Powerful" — tuyên bố, không hỏi. Câu hỏi chỉ viral trên kênh đã lớn. Với kênh nhỏ, statement dứt khoát + hằng-số-bị-phá là register cần thử nghiêm túc.
3. **Engagement thấp bất thường (like/view 0.7-1.7% vs 6-10% nhóm thắng).** Có thể là hệ quả của seed-audience chưa "đã" chứ không phải nguyên nhân — nhưng đáng theo dõi như chỉ báo sớm.
4. **Chưa khai thác công thức trần cao nhất của niche: "Part N" serialized recap** (7M views đã được chứng minh). Cấu trúc "một câu chuyện lớn cắt thành nhiều Short 60s có cliffhanger" vừa tăng completion (mỗi phần ngắn) vừa tạo binge-chain kéo audience quay lại kênh.
5. **Chưa có số retention/VVSA thật từ YouTube Studio trong vòng lặp quyết định.** Toàn bộ chẩn đoán "trượt seed-test" hiện dựa trên suy luận từ view — cần Master lấy 2 số cho 5-10 video gần nhất: "Viewed vs swiped away" và retention curve (đặc biệt % rớt ở 3 giây đầu). Không có số này, mọi tinh chỉnh hook đều là bắn trong sương.

**Đối thủ cần theo dõi duy nhất: SkarShorts.** Các kênh view khủng khác đã chết trên Shorts; SkarShorts đang sống, đăng 6.4/tuần, median 101k, công thức công nghiệp hóa được — giống mô hình pipeline của mình nhất. Khoảng cách không phải chất lượng render mà là: công thức nội dung (power-fantasy statement, biến thể nhân vật hot), duration kỷ luật 60s, và title statement.

## 5. Việc cần số liệu từ Master (không tự lấy được)

1. YouTube Studio → 5-10 Shorts gần nhất → **"Viewed vs swiped away"** (%).
2. Retention curve của 2 video tốt nhất vs 2 video ~500 view: % sống sót sau 3s, % hoàn thành.
3. Traffic source: bao nhiêu % view đến từ Shorts feed vs Search (để định vai trò title).

## 5b. Số thật từ YouTube Studio (Master cung cấp 2026-07-12, kỳ 13/6-11/7)

Tổng: 30,259 views · 203.96h watch time · +19 subs · 12,767 thumbnail impressions (CTR 1.84%).

- **Attention budget ~24 giây/view, gần như hằng số** (203.96h ÷ 30,259 = 24.3s; per-video dao động 13-42s, cluster 20-30s) — bất kể video dài 46s hay 111s. Hệ quả trực tiếp: video 46-57s đạt avg % viewed **42-46%**, video 85-111s chỉ **24-34%**. Cùng túi attention, kéo dài video chỉ làm giảm completion → trượt gate phân phối.
- **Không video nào ≥85s vượt 1,499 view. Top-3 của kênh đều 46-57s** (GR Penance listicle 56s = 5,986; Juggernaut 46s = 2,360; Doom losses 57s = 1,527). Batcave 49s mới 1 ngày đã 1,124 — quỹ đạo tốt.
- **Kênh KHÔNG bị chặn**: từ 17/6, MỌI video được seed ổn định 888-1,527 view trong 1-2 ngày đầu (trước 17/6 kênh lạnh: 13-272). "Trần 1k" của kênh = kích thước seed wave, đúng như research.
- **Second-wave có thật và đã xảy ra 1 lần**: GR listicle Jul 6 (2,429) → Jul 7 (747) → **Jul 8 (2,319, đợt đẩy lại)**. Video một-đợt điển hình: Doom losses 1,236 → 190 → 82 → 19. Vượt gate = được đẩy tiếp; không vượt = chết trong 3-4 ngày.
- CTR không phải lever chính: views (30k) >> impressions (12.7k) → phần lớn view từ feed swipe, khớp kết luận thuật toán mục 3.

→ Mục 4 "Cái sai #1 (duration)" giờ được xác nhận bằng CHÍNH dữ liệu kênh, không chỉ dữ liệu đối thủ. Target duration hợp lý: **40-55 giây finished** (24s attention ÷ 45s ≈ 53% completion, đủ chạm vùng vượt gate mà vẫn kể được chuyện).

---

## 6. Phase 2 — đối chiếu code hiện có với 5 kết luận audit

Map read-only (verify bằng grep/read thật):

| Kết luận audit | Code hiện tại | Trạng thái |
|---|---|---|
| Duration 61-76s vượt vùng thắng 34-63s | Mỗi mode có band riêng, đều hardcoded: recap `_TARGET_WORDS_MIN/MAX=195-245` (write_script.py:41-42, ~61-76s @3.4wps); **Q&A `_QA_TARGET_MIN/MAX_SEC=45-65` (explore_answer.py:53-54) — đã GẦN vùng thắng**; micro_moment 120-200w ≈ 35-59s (micro_moment.py:56-57) — đã trong vùng | Vấn đề thật chỉ còn ở recap + trần 65s của Q&A hơi vượt 63s |
| Title statement vs câu hỏi | YouTube title chỉ qua agent `title-smith.md`, register hiện tại = meme-flip; Q&A banner = câu hỏi verbatim (explore_answer.py:481); title-smith KHÔNG có section Q&A | Gap: chưa có statement register; title-smith chưa phân biệt mode |
| Hook 3s đầu | Cold-open picker có scoring + letterbox penalty đã fix (shots.py:1672, 1761); `COLD_OPEN_LOCK` env ghim tay; money_shot funnel chỉ chạy cho Q&A (answer_pipeline.py:59-66) | Nền tốt; chưa có gì đo/ép "câu đầu = statement mạnh nhất" |
| Part-N serialized recap | `stages/longform/` chỉ xuất MỘT video 8+ phút (concat N segment); grep "cliffhanger/Part" = 0 kết quả | **CHƯA CÓ — gap lớn nhất, công thức 7M view đang bỏ trống** |
| Loop | `SEAMLESS_LOOP=1` default, `_close_loop()` clone panel shot cuối = shot đầu, z khớp 1.0; outro card OFF (config.py:325) | Đã tối ưu, giữ nguyên |
| Pacing (phụ) | `MAX_SHOT_SECONDS=9999` — split sub-shot theo pacing đối thủ đang TẮT (shots.py:175, env chỉnh được, gợi ý ~2.6) | Có sẵn knob, chưa bật |

Mismatch phát hiện thêm: `config.py:169` default `TTS_PROVIDER="cartesia"` trong khi memory cũ ghi resemble — cần verify .env trước khi tin bên nào.

## 7. Đề xuất cải tiến (xếp theo tác động ÷ công sức — chưa sửa gì, chờ duyệt)

**Nhóm A — zero-code, làm ngay tuần này:**
1. **Đổi title register sang STATEMENT cho 5 video kế tiếp** (đặt title lúc upload, không cần sửa pipeline): công thức "hằng-số-bị-phá phát biểu dứt khoát" — kiểu "The One Man Even Ghost Rider Couldn't Judge" thay vì câu hỏi. Đo sau 10-14 ngày so median 1,150 hiện tại.
2. **A/B pacing bằng env** (nửa video theo quy trình A/B preview): `MAX_SHOT_SECONDS=2.6` — cắt shot nhanh như đối thủ, không sửa code.
3. **Master lấy 3 số YouTube Studio** (VVSA, retention 3s, traffic source) — mọi quyết định hook sau này neo vào số này.

**Nhóm B — sửa code nhỏ (cần duyệt; sau khi có số Studio, đây là đòn bẩy #1):**
4. Hạ band duration theo attention budget 24s đo được: `_QA_TARGET_MIN/MAX_SEC` 45-65 → **40-55**; recap band 195-245 words → **~140-180 words** (~45-55s finished). Lưu ý: recap band coupled với benchmark (word_min 165, punch rules) — phải retune benchmark cùng lúc.
5. Thêm section Q&A + statement-register vào `.claude/agents/title-smith.md` (hiện agent chỉ biết meme-flip cho recap).

**Nhóm C — feature mới, trần cao nhất (cần duyệt + spec riêng):**

6. **Mode "Part N" serial**: tái dùng decompose của `stages/longform`, nhưng thay vì stitch thành 1 video dài → xuất N Short riêng ~45-60s, cắt tại cliffhanger (chọn theo beat impact có sẵn của LOGIC_CRITIC), banner "Part N", câu cuối mỗi part tease part sau, part cuối loop về part 1. Một truyện = 3-5 Short → cadence tăng gấp rưỡi mà không thêm công scout. Đây là công thức đã được chứng minh 7M view và kênh giữ nó đã rời sân.

---

## 8. Audience-eye audit (2026-07-12): tải 7 video xem bằng mắt — mình vs họ

Tải thật 7 video (3 Grimframe: top 5.9k / mới 1.27k / flop 19 view — 4 đối thủ: SkarShorts 693k, Cosmo 1.2M, ComicCut 32k, RealQuickComics 7M), trích hook frame 0.3-3s + contact sheet + transcript + loudness, Opus chấm như fan comic lướt feed. Data: `scratchpad/videos/` (session 0e9a370b).

**Kết luận 1 câu: Grimframe KHÔNG thua ở kịch bản/giọng kể (ngang hoặc hơn đối thủ) — thua gần như toàn bộ ở LỚP HÌNH ẢNH 3 giây đầu + năng lượng audio.**

Bảng điểm trung bình (6 tiêu chí, 1-5): cosmo 4.5 · rqc 4.5 · skar 4.5 · **grim_top 3.7** · comiccut 3.5 · **grim_new 3.2** · **grim_flop 2.0**. Swipe-test 3s: 2/3 video Grimframe bị LƯỚT (grim_new: nửa trên blur + panel nhỏ, hình mạnh nhất đến sau giây 3; grim_flop: chữ cover lật ngược = AI-slop signal).

Top 5 gap (ảnh hưởng giảm dần) + trạng thái code:
1. **Chữ lật ngược trên frame 1** (grim_flop) — video render TRƯỚC fix `MIRROR_PANELS=false` (2026-07-07); video mới không còn mirror. Việc còn lại: cân nhắc xóa/re-render các video CŨ còn dính lỗi này trên kênh (flop sâu, có thể gieo tín hiệu xấu).
2. **3 giây vàng bị phí** (grim_new, render SAU gói fix 07-07): cold-open vẫn chọn panel mờ/nhỏ, hình đắt nhất ("I AM BANE") đến sau giây 3. Khớp mục "còn mở" trong memory: `_cold_open_panel` aspect_fit vẫn là soft score. FIX đề xuất: money-shot-first cho cold-open (Q&A đã có money_shot funnel — nâng thành binding cho frame 1; recap thêm cơ chế tương tự).
3. **Blur letterbox làm phong cách chủ đạo** thay vì full-bleed: panel nhỏ trôi trên nền mờ → loãng, kém "đã". FIX: mặc định crop 9:16 full-bleed, blur chỉ là bất khả kháng.
4. **Audio nhỏ hơn nhóm thắng ~5 LUFS** (grim -24.6/-24.9 vs skar/cosmo/rqc -19.4/-20.3; YouTube chỉ hạ video to, KHÔNG kéo video nhỏ lên → mình phát nhỏ thật trong feed). Nghi phạm đã khoanh: `stages/stage_5/audio.py:13` có `loudnorm I=-14` nhưng CHỈ ở nhánh mix BGM; nhánh fallback "TTS-only copy" bỏ qua loudnorm. FIX: loudnorm cả nhánh TTS-only (+ xem lại có nên bật BGM).
5. **Bong bóng thoại trống còn lọt + caption header mảnh khó đọc lướt** — bubble-avoid crop 07-07 chỉ trượt cửa sổ, không xử panel nhỏ trên blur; caption đối thủ to-đậm-giữa. FIX: mở rộng bubble handling + A/B caption to-giữa.

3 thứ ĐỪNG đổi (đang ngang/hơn): wording lời kể (hook/tease/kết mirror), đa dạng màu theo nhân vật, style caption bo viền đậm (chỉ cần to hơn).
