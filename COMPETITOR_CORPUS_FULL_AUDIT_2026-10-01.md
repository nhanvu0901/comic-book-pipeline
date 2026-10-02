# Phân tích toàn bộ corpus Shorts đối thủ — 2026-10-01

Tài liệu này tổng hợp 3.397 transcript (ASR Whisper qua Groq) và 3.815 tiêu đề của 5 kênh đối thủ, để rút ra quy tắc cho writer micro, writer Q&A, scout và tiêu đề. Các audit trước (COMPETITOR_SCRIPT_AUDIT_2026-09-28, HOOK_OPENING_AUDIT_2026-09-30, HANDOFF_COMPETITOR_CORPUS_MICRO_2026-10-01) dựa trên 815–2.034 transcript; tài liệu này thay chúng ở mọi chỗ số liệu khác nhau.

## 0. Tóm tắt

1. **Không ai kết bằng câu nhử hay CTA.** 0/3.397 video có "subscribe", "like", "follow", "part 2", "stay tuned"; 1 video có "comment". Câu nhử ở 2 câu cuối chỉ có 31 video (0,9%). Theo nhãn đọc tay, 57% kết bằng kết cục, 3% kết bằng twist không nói ra. Hướng "kể ra kết cục, không để lửng" của prompt micro là đúng chuẩn thị trường.
2. **Kiểu câu kết không quyết định view.** Sau khi bù tuổi video, `ending_type` không có ý nghĩa thống kê (p=0.17), `twist_handling` cũng không (p=0.27). Thứ có ý nghĩa: khoảnh khắc chính kể mơ hồ (`main_moment=partial`, n=265) kém rõ rệt ở cả 3 kênh (era-pct 0.39 so với 0.50). Kết lửng kiểu cắt ngang (`abrupt`) là nhóm kém nhất.
3. **Tín hiệu cơ học mạnh nhất là hook lặp lại đúng nội dung tiêu đề** (ρ=+0.11, 4/4 kênh). Tiếp theo: hook mở bằng "This is the time/why/how…" (+6,7 điểm phần trăm ở top quartile), hook có so sánh nhất ("most", "strongest", OR 1.99), hook có con số (OR 1.56).
4. **Độ dài: 130–210 từ là vùng an toàn** (≈ 45–60 giây). Dưới 110 từ và từ 210 từ trở lên đều yếu hơn. Video từ 60 giây trở lên kém ở ComicsUnlocked và ComicCandid. Tốc độ nói (WPM) không ảnh hưởng. Script micro hiện của mình (60–80 từ) ngắn hơn hẳn mặt bằng đối thủ (136–176 từ).
5. **Chủ đề quan trọng hơn câu chữ.** Image (Invincible, The Boys) vượt trội mạnh nhất cả corpus (AUC 0.64). Format list/countdown và power_versus vượt; issue_recap (kể lại cả issue) kém. Spider-Man, Doctor Doom, Green Goblin kém; Batman, Superman chỉ trung bình dù được làm nhiều nhất.
6. **Cùng một câu chuyện, đăng lại với hook/tiêu đề khác, chênh nhau 8–28 lần view (trung vị), có cặp chênh 1.000 lần.** Bản thắng thường là bản ngắn hơn và nêu mấu chốt sớm hơn. Đóng gói (hook + tiêu đề + thumbnail) quyết định nhiều hơn bản thân câu chuyện.
7. **Bối cảnh: đủ một câu là tốt, dài thì hại.** Tổng thể `setup_before_main` và `context_behind` không có ý nghĩa thống kê. Nhưng trong từng kênh, dẫn dắt dài trước khi tới cảnh mà tiêu đề hứa thì kém rõ: ComicsUnlocked trung vị 96k so với 305–408k; ComicCandid 2+ câu setup chỉ đạt mức "gấp đôi trung vị" bằng một nửa (8% so với 15%).

## 1. Dữ liệu và phương pháp

### 1.1 Corpus

| Kênh | Video trong danh sách | Có transcript | View trung vị | p10–p90 | Thời lượng trung vị | Số từ trung vị |
|---|---:|---:|---:|---|---:|---:|
| ComicsUnlocked (CU) | 1.321 | 1.319 | 245.000 | 23k–2,4M | 43 s | 136 |
| Comic_Escape (CE) | 1.098 | 1.079 | 36.000 | 12k–1,2M | 54 s | 173 |
| ComicCandid (CC) | 860 | 860 | 28.000 | 11k–100k | 44 s | 146 |
| Flikey (FL) | 134 | 132 | 2.100.000 | 330k–6,9M | 58 s | 176 |
| comiczyt (CZ) | 402 | **7** | 122.000 | 2,8k–2,8M | 131 s | 456 |

- 411 MP3 chưa transcribe (395 của comiczyt, 16 của Comic_Escape) vì Groq hết rate limit. comiczyt chỉ dùng được tiêu đề + view; mọi số transcript của nó đánh dấu yếu.
- Mỗi video có view (làm tròn) và tiêu đề từ `*_shorts.tsv`. Không có ngày đăng.

### 1.2 Cách làm

- **Đặc trưng cơ học** (script `quant.py`): câu đầu (hook) và câu cuối, số từ, thời gian tới hết câu đầu (từ SRT), WPM, độ dài câu, cờ CTA và câu nhử, độ trùng tiêu đề–hook (`utils/lexical_sim.token_jaccard`), đặc trưng tiêu đề trên cả 3.815 video.
- **Nhãn đọc tay bằng LLM**: 8 agent đọc toàn văn từng transcript và gán 21 trường theo schema cố định (`label_schema.json`): format, kiểu hook, lượng setup, bối cảnh, khoảnh khắc chính rõ hay mơ hồ, có kể hậu quả, cách xử lý twist, kiểu câu kết, CTA, ngôi kể, thì, giọng, nhân vật, publisher… 3.397/3.397 dòng hợp lệ, 0 trùng, 0 lỗi trường.
- **Chuẩn hoá view**: view giữa các kênh chênh ~60 lần nên mọi so sánh dùng **phân vị view trong cùng kênh**, không so số thô.
- **Bù tuổi video**: thứ tự trong danh sách là mới → cũ, và tuổi tương quan mạnh với view (Spearman vị trí–view: CU +0.67, FL +0.49, CE +0.17, CC +0.07). Vì vậy có thêm cột `era-pct` = phân vị so với ~2W video đăng gần nhau. **Chỉ kết luận những gì đứng vững ở cả hai cột.** Nhiều kết luận cũ về độ dài/tốc độ nói không qua được bước này.
- Kiểm định: Spearman, Kruskal-Wallis, Mann-Whitney, hiệu chỉnh BH. Hiệu ứng nào cũng nhỏ (|ρ| ≤ 0.11, eps² ≤ 0.015): đây là dữ liệu tương quan nhiễu, không phải công thức chắc thắng.

## 2. Hook (câu mở đầu)

### 2.1 Kết quả vững (qua bù tuổi, cùng dấu ở ≥3/4 kênh)

| Đặc trưng | n | ρ (era) | Kênh cùng dấu | Hiệu ứng |
|---|---:|---:|---|---|
| Hook trùng nội dung tiêu đề (`title_hook_jaccard`) | 3.390 | +0.11 | 4/4 | ×1.17 view mỗi +1 SD |
| Hook mở bằng "This is the time / why / how…" | 683 (20%) | +0.08 | 3/4 | top quartile +6,7 điểm %, OR 1.79 |
| Hook có so sánh nhất (most, strongest, best…) | 466 (14%) | +0.07 | 4/4 | +7,5 điểm %, OR 1.99 |
| Hook có con số | 626 (19%) | +0.05 | 4/4 | +5,7 điểm %, OR 1.56 |

Không vững / không có tác dụng:
- Hook dạng câu hỏi (21%): trung tính tổng thể; ComicsUnlocked hơi âm.
- Hook mở bằng tên nhân vật hoặc mệnh đề phụ "After/When/While…": xu hướng âm, chưa đủ ý nghĩa. Riêng Comic_Escape, mở bằng mệnh đề phụ kém rõ (−12 điểm %).
- Theo nhãn: `hook_type=superlative` là giá trị duy nhất vượt có ý nghĩa (n=283, era-pct 0.55, q=.012).

### 2.2 Độ dài hook

| Số từ câu đầu | Era-pct pooled (n) |
|---|---|
| 0–8 | 0.45 (608) |
| 8–12 | 0.54 (808) |
| 12–16 | 0.52 (634) |
| 16–20 | 0.47 (392) |
| 20–30 | 0.52 (528) |
| ≥30 | 0.42 (420) |

Vùng tốt là **8–16 từ**. Hook ≥30 từ (chủ yếu Comic_Escape, kiểu chuỗi "After X, Y happened, leading Z…") là nhóm kém nhất. Hook rất ngắn (≤8 từ) chỉ tốt ở ComicsUnlocked theo cột chưa bù tuổi, sau bù tuổi thì không.

### 2.3 Mẫu hook của video đầu bảng (ví dụ, video ID + view)

- Đặt mấu chốt/rủi ro ngay câu một: EjugFqHl1eA (15M, Deadpool và đứa con gái 97 tuổi), -HMS-JXhPS0 (24M, Homelander giết Tổng thống), NNyydizMSSA (5,0M, nêu cái chết ngay trong hook).
- Câu hỏi về một điều ai cũng tò mò hoặc phản trực giác: OiK7zYakjQQ (21M, "Wolverine mạnh hơn khi không có adamantium?"), FG1TE1v_5uk (9,8M, "Vì sao Flash tránh nước tăng lực?").
- So sánh nhất kèm countdown: T52AmoEmiGs (16M, "ba sức mạnh mutant vô dụng nhất"), AwTMifpoFmE (23M, "ba đứa con mạnh nhất của Wolverine").
- Nhân vật được yêu thích + một từ về số phận/cảm xúc: ZTlyRUXbPm0 (10M, Galactus làm tang lễ cho Silver Surfer), PkUL9tvJang (7,2M, "cái chết tàn bạo của Cecil Stedman").
- Hành động bình thường của phản diện, có tính mỉa mai: a-IxEA-Vvz4 (15M, Thanos dắt bà cụ qua đường).

## 3. Cấu trúc: bối cảnh, khoảnh khắc chính, hậu quả

### 3.1 Khoảnh khắc chính phải rõ

| `main_moment` | n | Era-pct | AUC | q |
|---|---:|---:|---:|---|
| clear | 3.124 | 0.50 | 0.58 | <.001 |
| partial | 265 | 0.39 | 0.42 | <.001 (3/3 kênh) |

Đây là kết quả cấu trúc vững nhất: video mà người xem không hiểu rõ chuyện gì xảy ra thì kém ở mọi kênh. Nguyên nhân thường gặp: dàn quá nhiều nhân vật, kể lại một round giữa arc (DC K.O., arc Doom Sorcerer Supreme), hoặc tiêu đề hứa một cảnh mà script không bao giờ kể (SyYgehHKB4o 5,1k: trận đấu hoá ra là giấc mơ).

### 3.2 Setup và bối cảnh

- Tổng thể `setup_before_main` (p=0.13) và `context_behind` (p=0.11) không có ý nghĩa thống kê.
- Trong từng kênh, bối cảnh **dài trước khi tới cảnh mà tiêu đề hứa** thì hại:
  - ComicsUnlocked: 125/229 video truyện có dẫn dắt dài, trung vị 96k so với 305–408k.
  - ComicCandid: 78% không có setup; nhóm 2+ câu setup đạt "≥2× trung vị" 8% so với 15%.
  - ComicsUnlocked lô 3: trong single_moment, **đúng một câu** bối cảnh là tốt nhất (226k), không có câu nào (77k), 2+ câu (204k).
- Kiểu viết thắng: một mệnh đề động cơ ("vì hắn đã chán Gotham") rồi vào cảnh; pivot bằng "However," (55–81% script của CU/CE).

### 3.3 Kể hậu quả (aftermath)

- 69% video kể hậu quả / kết thúc ra sao; dưới 2% dừng ngay ở sự kiện.
- Theo kênh: video chỉ kể **một phần** hậu quả kém hơn kể đủ (ComicCandid: đạt ≥2× trung vị 4% so với 15%; ComicsUnlocked lô 3: 75k so với 197k).
- Một mệnh đề là đủ: "Thor quay về Asgard và dùng mũ của Galactus làm đồ trang trí" (QQ1mr1MK9sk).

## 4. Câu kết, twist và câu nhử

### 4.1 Phân bố

| Kiểu kết | Tỷ lệ (pooled, đủ 3.397 nhãn theo từng memo) |
|---|---|
| outcome (nói rõ kết cục/hệ quả) | ~57–63% |
| twist_revealed (twist được nói ra) | ~11% |
| reflection (bình luận ý nghĩa) | ~9% |
| punchline (câu đùa) | ~6–9% |
| teaser_unrevealed (nhử mà không nói) | ~3–4% |
| abrupt (cắt ngang) | ~1–4% |
| câu hỏi cho người xem / CTA | gần 0 |

### 4.2 Tác động lên view

- `ending_type` tổng thể: không có ý nghĩa (p=0.17). Kết bằng outcome là chuẩn, không phải lợi thế.
- **Kém rõ:** `abrupt` (era-pct ~0.35–0.40; ComicCandid trung vị 19k); issue_recap kết bằng twist (0.39, n=155) hoặc reflection (0.41, n=73).
- **Twist nhử mà không nói** (`teaser_unrevealed`, ~60 video ở mức nhãn, n nhỏ, biến thiên lớn):
  - Single scene kết bằng một hình ảnh đáng sợ còn treo (single_moment, era-pct 0.63, n=39, yếu): không hại.
  - Recap dừng giữa arc: hại (ComicsUnlocked lô 3: trung vị 37k, n=13).
  - Comic_Escape lô 5: 7/8 video nhử ở mức 14–63k, dưới trung vị 36k; ngoại lệ duy nhất (hCnB2X-pcMU 4,7M) kết bằng một lựa chọn cảm xúc chưa giải quyết, không phải một sự thật bị giấu.
  - Kết luận: **giấu một sự thật (twist, danh tính, kết cục) không giúp và thường hại**; chỉ có "để hình ảnh/cảm xúc treo" là chấp nhận được.
- Ở top quartile Comic_Escape, các kiểu kết twist/quip/reflection/abrupt/teaser chiếm 15%, ở bottom quartile 27%. Video Comic_Escape 1M+ kết bằng hệ quả nói rõ 66% và bằng reveal 5% (dưới 20k: 51% và 18%).

## 5. Độ dài và nhịp

| Số từ | Era-pct pooled (n) | Ghi chú |
|---|---|---|
| 0–110 | 0.43 (217) | yếu; ở CU thì gần trung tính (0.48) |
| 110–130 | 0.49 (541) | |
| 130–150 | 0.50 (733) | |
| 150–170 | 0.53 (810) | tốt nhất cùng 170–210 |
| 170–190 | 0.52 (597) | |
| 190–210 | 0.54 (265) | |
| ≥210 | 0.41 (227) | yếu ở mọi kênh |

- Thời lượng ≥60 s: 0.42 pooled (CU 0.42, CC 0.21).
- WPM: không ảnh hưởng sau bù tuổi (ρ ≈ 0.008).
- Sweet spot theo từng kênh (memo): CU 110–140 từ, CE 150–200 từ, CC 120–170 từ.
- Kết luận: **khoảng 120–190 từ, dưới 60 giây**. Script micro hiện tại của pipeline (35–90 từ, trần ~145) nằm dưới vùng này.

## 6. Giọng kể và ngôn ngữ

- 98% ngôi thứ ba; gần như không ai nói "you" hay hỏi người xem.
- Thì: CU và CC chủ yếu hiện tại, CE chủ yếu quá khứ; thì không ảnh hưởng view (p=0.45).
- Giọng vượt trội (n nhỏ): deadpan — kể chuyện vô lý/ghê rợn bằng giọng phẳng (n=58, era-pct 0.77, q=.002), hype (n=61, 0.73, chỉ CU). Giọng dark (0.45) và humorous kéo dài thì kém.
- Câu đùa hiếm (14–27% video có ít nhất một câu); nhiều câu đùa (2+) không giúp. Một câu đùa dựa trên chi tiết thật là đủ.
- Từ né kiểm duyệt ("unalived", từ bị che bằng dấu sao) xuất hiện ở cả CU và CE.
- Lỗi ASR: ~30 video kết bằng "Thank you." / "you" là ảo giác Whisper, đã loại khỏi phân tích câu kết.

## 7. Format và chủ đề

### 7.1 Format (era-pct, pooled)

| Format | n | Era-pct | Ghi chú |
|---|---:|---:|---|
| list_every_time (countdown 3-2-1) | 227 | 0.61 | vượt ở 3/3 kênh; CE: 14% video nhưng 27% nhóm 1M+ |
| power_versus | 190 | 0.60 | vượt ở 2/3 kênh |
| event_recap | 35 | 0.70 | chỉ CE, n nhỏ |
| lore_fact | 209 | ~0.5 | CU mạnh, CE yếu |
| single_moment | 982 | ~0.48 | trung tính; CC đạt "≥2× trung vị" 19% so với 7% của issue_recap |
| issue_recap | 1.096 | 0.47 | kém ở 3/3 kênh |
| adaptation (phim/game) | 41 | — | kém nhất theo memo (CU 24–31k) |

### 7.2 Nhân vật và publisher (theo nhãn, n ≥ 30)

| Nhóm | Nhân vật / publisher | n | Mean era-pct | q |
|---|---|---:|---:|---|
| Vượt | The Boys | 70 | 0.66 | <.001 |
| Vượt | Invincible | 165 | 0.62 | <.001 |
| Vượt | publisher Image | 223 | 0.71 (median) | <.001 (CE, CC; CU bằng 0) |
| Kém | Spider-Man | 413 | 0.44 | <.001 |
| Kém | Doctor Doom | 132 | 0.43 | .046 |
| Kém | Green Goblin | 35 | 0.34 | .025 |
| Sát ngưỡng | Thor | 100 | 0.42 | .096 |
| Trung bình | Batman 587, Superman 444, Wolverine 222, Deadpool 217, Hulk 193, Joker 173 | | ~0.5 | |

Chủ đề tiêu đề vượt trội (3.815 tiêu đề): power_rank ("strongest", "powers", AUC 0.59), top_n_list (AUC 0.57), alt_universe và origin/how/why (hơi vượt). Villain-focused (AUC 0.42) kém.

### 7.3 Kiểu câu chuyện thắng (memo của 8 lô)

- Một nhân vật lớn bị làm nhục hoặc thất bại trong một cảnh (ComicsUnlocked "humiliates a powerhouse" trung vị 633k; ComicCandid guxRSL-MDlI 1,7M, pZGhgyfdAwI 2,2M).
- Cái chết hoặc số phận của một nhân vật phụ được yêu thích trong franchise đang hot ("what happened to X", "how X died": Ci3I4IbEjYM 6,9M, URKAz8XTEbA 7,7M).
- Sự thật sức mạnh phản trực giác (healing factor, adamantium).
- Cảnh mất mát/bên mộ (tGHGJP3Vcts 3,7M, Thor bên mộ Cap), thú cưng, giải cứu trẻ bị bạo hành.
- Hành động bình thường của phản diện có tính mỉa mai; chuyện ghê rợn/kinh dị kể phẳng.

Kiểu thua: recap giữa arc, crossover Marvel–DC/hoạt hình, tiểu sử nhân vật ít người biết không gắn franchise, giả thuyết phim.

## 8. Tiêu đề và đóng gói

- Độ dài tiêu đề tăng nhẹ theo view (ρ +0.08, 5/5 kênh).
- Từ xếp hạng (most/best/worst/strongest) và mở bằng con số: xu hướng dương, chưa đạt ngưỡng vững.
- Mở tiêu đề bằng tên nhân vật: hơi âm (Comic_Escape −0.14).
- Emoji, dấu hỏi, hashtag, chữ IN HOA: không tác dụng sau bù tuổi.
- **Đăng lại cùng câu chuyện:** CU lô 2 có 20 cặp cùng script, chênh trung vị 28 lần (bản thắng ngắn hơn ở 12/20 cặp); CU lô 3 có 30 câu chuyện xuất hiện 2–4 lần, chênh trung vị 7,8 lần; Flikey đăng lại cùng script (5jeD_k7QwQU 1,6M so với w5D_ZYobMNE 5,4M). Cùng một câu chuyện "Joker giết Batman": Flikey 8,2M, Comic_Escape 35k.
- ~15% tiêu đề hứa một điều mà script bỏ qua, nói ngược, hoặc chỉ tới ở câu cuối; nhóm này dưới trung vị.

## 9. Flikey — vì sao view gấp ~54 lần Comic_Escape với cùng độ dài

- Nhân vật cực nổi tiếng (Spider-Man, Batman, Superman) với khoảnh khắc cảm xúc hoặc cấm kỵ, không phải nhân vật ít người biết.
- Một câu chuyện nhỏ trọn vẹn: 63% single_moment, 67% chỉ một câu setup, 80% kể hậu quả.
- Câu ngắn (~18 từ/câu so với ~34 của CE), giọng chủ quan ("beloved", "unfortunately").
- Deadpan/humorous (4,5M / 3,6M) vượt dark (1,4M); reveal nằm ở câu cuối nhưng được nói ra.
- Lưu ý: Flikey đăng lại nhiều nên một phần chênh lệch là do độ phủ kênh, không chỉ do script.

## 10. Áp dụng cho pipeline

Các đề xuất dưới đây **chưa được áp vào code/prompt**; cần Master duyệt.

### 10.1 Writer micro (`prompts/gemini_micro_moment_writer.md`)

Giữ nguyên (được dữ liệu ủng hộ):
- Kể ra kết cục; không câu nhử, không CTA, không hỏi người xem.
- Khoảnh khắc chính phải rõ cho người mới (lý do vững nhất của cả corpus).
- Một mệnh đề / một câu bối cảnh rồi vào cảnh.

Đề xuất thay đổi:
1. **Nâng độ dài mục tiêu** từ 35–90 từ (trần ~145) lên **khoảng 110–170 từ khi fact sheet có đủ beat đã kiểm chứng** (aftermath + context). Không độn: khi sheet mỏng vẫn được viết ngắn. Đây cũng là điều Master muốn ("viết thêm phần chính hoặc giải thích phần còn lại của cốt truyện").
2. **Hook 8–16 từ, nêu mấu chốt/kết quả ngay**, ưu tiên lặp lại đúng ý của tiêu đề; cho phép mở kiểu "This is the time/why/how…" và so sánh nhất khi có nguồn. Tránh hook ≥30 từ dạng chuỗi "After X, Y…".
3. Cho phép **outcome-first**: nêu kết quả ở hook, thân bài giải thích vì sao/như thế nào (CU lô 2: 549k so với 270k khi kể theo trình tự, ở script ≤150 từ).
4. Một câu đùa tối đa, chỉ khi dựa trên chi tiết thật; giọng phẳng cho chi tiết vô lý/ghê rợn.

### 10.2 Writer Q&A (`prompts/gemini_qa_writer_template.md`)

- Countdown đúng 3 mục, 3→1, có từ so sánh nhất (kể cả so sánh nhất tiêu cực: "vô dụng nhất").
- Trả lời câu hỏi trong 2 câu đầu, rồi bằng chứng; mỗi mục một sự kiện có kết quả.
- **Ghi rõ issue**: chỉ 12–15% video đối thủ nêu tên comic/issue; "every time X" có số issue sẽ khác biệt.
- Tránh: danh sách 7 mục, câu trả lời lửng, góc phim/TV, danh sách nhân vật ít người biết không gắn franchise.

### 10.3 Scout

- **Xếp hạng theo franchise**: cộng điểm Invincible, The Boys (Image); trừ nhẹ Spider-Man, Doctor Doom, Green Goblin, Thor; Batman/Superman trung tính.
- **Ưu tiên kiểu câu chuyện thắng** ở mục 7.3; tránh recap giữa arc và cảnh cần biết nhiều issue trước.
- **Lọc trùng theo cốt truyện, không theo tiêu đề**: đối thủ đăng lại cùng câu chuyện dưới tiêu đề mới. Dùng `quant/covered_topics.json` (3.815 tiêu đề, nhân vật, view, phân vị) và danh sách "already covered" trong 8 memo.
- **Đã bị làm ≠ phải tránh**: câu chuyện được kể lại nhiều lần là bằng chứng có nhu cầu (yellow paint, Harley dungeon, Batman giết Joker, Failsafe…). Có thể làm nếu mình có góc hoặc bằng chứng tốt hơn, nhưng xếp hạng thấp hơn câu chuyện chưa ai làm.

### 10.4 Tiêu đề (title-smith)

- Tiêu đề và hook phải nói cùng một điều (tín hiệu vững nhất).
- Từ so sánh nhất và con số khi có thật; tránh mở bằng tên nhân vật trơn.
- Không hứa điều script không kể.

## 11. Hạn chế

- Tương quan, không phải nhân quả. Không có ngày đăng thật, retention, tỷ lệ lướt qua; view là tích luỹ trọn đời.
- Thumbnail không được phân tích, mà dữ liệu đăng lại cho thấy đóng gói chiếm phần lớn chênh lệch.
- Nhãn LLM có sai số phán đoán; quy ước ranh giới (ví dụ event_in_progress với shock_statement, abrupt với teaser) có thể lệch nhẹ giữa các lô; các memo ghi rõ quy ước.
- comiczyt gần như không có transcript; Flikey chỉ 132 video.
- Hiệu ứng đều nhỏ; ngoài franchise/format, không có đặc trưng nào giải thích quá vài phần trăm phương sai.

## 12. File và cách chạy lại

Thư mục (git bỏ qua): `research/competitor_audio_2026-09-28/analysis_2026-10-01/`

- `corpus.jsonl` — 3.397 transcript + view + tiêu đề; `titles_all.jsonl` — 3.815 tiêu đề.
- `label_schema.json`, `labels/slice_01..08.jsonl` — nhãn; `tools.py check <n>` — kiểm tra.
- `memos/slice_01..08.md` — phân tích định tính từng lô, có ví dụ video ID, phần SCOUT và danh sách đã làm.
- `quant.py` — `python3 quant.py` (~8 s) sinh lại `features.jsonl`, `quant/*.json`, `quant/report.md` (báo cáo định lượng đầy đủ), `quant/exemplars.md` (top/bottom 15 mỗi kênh kèm hook và câu kết), `quant/covered_topics.json`.
- Khi Groq hết giới hạn: chạy tiếp worker transcribe (`research/transcribe_worker.py`) cho 411 MP3 còn lại, dựng lại corpus, gán nhãn phần mới, rồi chạy lại `quant.py`.
