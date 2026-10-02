# Handoff: scout micro (You.com) và prompt Gemini writer — 2026-10-01

Nhánh `feat/scout-single-selection-flow`, đã push lên origin. Mọi commit dưới đây nằm trên nhánh này. Server Windows chưa được deploy đợt 2 và đợt 3 (server không vào được trong lúc làm).

## 1. Vấn đề ban đầu

Gemini viết script micro cho Captain America #15 (2026) và kết bằng:

> "The underworld showdown ends with a shocking twist centered on Steve, offering what looks like an overly easy solution to his problem."

Đó là một câu nhử: nó nói "có twist" nhưng không nói twist là gì. Nguồn gốc nằm ở hai tầng:

1. **Scout You.com** chỉ đi tìm cảnh chính. Nguồn duy nhất về phần kết là một bài review (AIPT) viết "a shocking twist I never saw coming", và scout chép câu phản ứng đó vào object như thể là sự kiện.
2. **Prompt Gemini** không cấm câu nhử, không đòi kể hậu quả, nên writer chép lại câu phản ứng của reviewer thành câu kết.

Yêu cầu của Master: không được kết lửng; thay vào đó viết sâu hơn về khoảnh khắc chính, hoặc kể/giải thích phần còn lại của cốt truyện. Scout phải tìm thêm "chuyện gì xảy ra tiếp" và "bối cảnh phía sau".

## 2. Đợt 1 — scout tìm hậu quả và bối cảnh, prompt cấm câu nhử

### 2.1 Scout (commit `cbf7557`, `08ffe32`, `0a221a2`, `6fce770`)

**Làm gì:** object scout micro có thêm 4 trường:

| Trường | Nghĩa |
|---|---|
| `aftermath` | chuyện gì xảy ra sau turning point trong cùng issue, cảnh kết thúc ra sao, twist được nhắc tới thực ra là gì |
| `context_behind` | điều gì dẫn tới khoảnh khắc: vì sao các nhân vật ở đó và đối đầu, mỗi bên muốn gì, vũ khí/năng lực từ đâu ra |
| `unrevealed` | kết cục mà nguồn chỉ úp mở, không nói rõ |
| `detail_citations` | `[{supports: aftermath|context_behind, url, quote}]` — mỗi ý phải có URL và một câu trích nguyên văn |

**Làm thế nào:**
- `stages/research_scout/planner.py`: thêm `MICRO_DETAIL_PROPS`; `compile_schema(plan, mode)` và `general_output_schema(mode)` theo mode, nên schema micro có 4 trường này còn Q&A thì không; thêm 4 tên vào `RESERVED_FIELD_NAMES`; thêm `_MICRO_DETAIL_RULES`.
- `research_prompts/general_micro.v1.md` (prompt dự phòng khi không có OpenRouter planner): thêm 3 luật:
  - trả `aftermath`/`context_behind` kèm `detail_citations` và đưa URL vào `evidence_urls`; để `""` khi không nguồn nào nói, không được suy đoán;
  - câu phản ứng hay câu nhử của reviewer ("a shocking twist I never saw coming", "everything changes") **không phải là sự kiện**, không được dùng làm `what_visibly_happens`, `turning_point` hay `aftermath`; reveal bị giấu thì ghi vào `unrevealed`;
  - chữ trong chính yêu cầu tìm kiếm ("final twist", "shocking reveal") không phải là bằng chứng.
- `research_prompts/specific_micro.v2.md` (vòng xác minh): ứng viên dựa trên câu phản ứng/câu nhử thay vì một sự kiện được nêu rõ thì là **NOT CONFIRMED**; `notes` phải nói nguồn nêu gì, chỉ úp mở gì, giấu gì.
- `stages/youcom_scout.py` `run_micro`: CLI micro cũng hỏi và in các trường mới.
- `stages/research_scout/workflow.py`: thêm `verification_summary(payload)`.
- `stages/research_scout/project_factory.py`: `build_scout_candidate(store, session, candidate, gate)` và `refresh_scout_candidate(project_name, ...)`. `scout_candidate` giữ 9 khoá cũ và thêm `turning_point`, `why_it_lands`, `aftermath`, `context_behind`, `unrevealed`, `detail_citations`, `reason` (lý do của gate), `scout_check` (kết luận của vòng xác minh).
- `ui/screens/s1_research_scout.py`: thẻ ứng viên hiện thêm "What happens next:", "Context:", "Not revealed by sources:".
- `.claude/agents/moment-scout.md`: cập nhật cho khớp.

**Vì sao:** writer chỉ kể được những gì nó được đưa. Nếu scout không tìm phần kết thì writer hoặc dừng lửng, hoặc bịa. Tách `unrevealed` ra khỏi `aftermath` để phần nguồn giấu không bị kể như sự thật.

### 2.2 Prompt Gemini micro (commit `0b02060`, `fc129fc`, `178dbaa`, `bb60dcf`, `cb1aa8a`, `5966e02`)

File: `prompts/gemini_micro_moment_writer.md`. Prompt chạy hai pha: PHASE 1 dựng fact sheet có mã beat (B1, B2…); Master gõ `WRITE` thì PHASE 2 mới viết script.

**Làm gì:**
- Phần INPUT giải thích `scout_check`, `reason`, `aftermath`, `context_behind`, `detail_citations`, `unrevealed`: đây là manh mối cho PHASE 1, không phải sự thật; `unrevealed` phải tự tìm, không được kể như đã viết.
- Mục mới **"Reactions and teasers are not beats"**: câu phản ứng của reviewer không được thành beat.
- Mục mới **"What happens next, and what set it up"**: beat AFTERMATH và CONTEXT trong fact sheet, cùng các dòng `AFTERMATH | CONTEXT BEHIND | OPEN QUESTIONS`.
- PHASE 2: có AFTERMATH thì kể nó diễn ra thế nào và kết ở kết cục; không có thì viết sâu hơn khoảnh khắc chính và kết ở chi tiết mạnh nhất đã kiểm chứng.
- Khối **NO TEASERS**, luật câu cuối, và bước tự kiểm: mỗi câu nhắc twist/reveal/ending/surprise phải nói cái đó là gì, nếu không thì là câu nhử và phải xoá.
- Output có thêm dòng audit `AFTERMATH CHECK` và `OPEN LOOPS`.
- `prompts/gemini_qa_writer_template.md`: mỗi mục phải kết ở chuyện thật sự đã xảy ra.
- `stages/stage_3/micro_moment.py` (writer không qua Gemini): cấm câu chỉ gợi ý; câu hỏi kết chỉ được hỏi về ý nghĩa, không hỏi "chuyện gì xảy ra tiếp".

**Code (`stages/stage_3/gemini_prompt.py`):**
- `178dbaa`: truyền object scout đầy đủ (kết luận xác minh, lý do gate, aftermath) vào prompt.
- `cb1aa8a`: `_drop_repeated_ends` bỏ hook bị đọc hai lần; `_AUDIT_LABEL_RE` cắt script ngay khi gặp tiêu đề audit (OPENING CHAIN, STORY FRAME CHECK, AFTERMATH CHECK, OPEN LOOPS, REACTIONS, UNSUPPORTED FACTS), để dòng audit không bị đọc thành lời.
- `5966e02`: khi project thiếu scout candidate thì dựng lại bằng `project_factory.build_scout_candidate` thay vì giữ một bản sao logic riêng.

**Kết quả:** đã deploy lên Windows; project Steve được làm mới `scout_candidate` và tạo lại writer prompt. Test Mac 1.882 pass; trên Windows chạy 297 test liên quan, đều pass.

## 3. Đợt 2 — sửa theo kết quả test bằng agy (commit `6cfb105`, `8abb508`, `62fa7c2`, `576b936`)

Test bằng agy CLI (Gemini 3.1 Pro, HOME cô lập, chỉ cho `search_web`):
- template cũ lặp lại đúng lỗi câu nhử;
- template mới không còn câu nhử, nhưng lộ ra 3 lỗi mới.

| Lỗi tìm thấy | Sửa | Vì sao |
|---|---|---|
| Gemini mở truyện bằng cách nói lại hook bằng chữ khác → hook bị đọc 2 lần | `6cfb105`: template ghi `<chosen hook, alone on its own line>` và "truyện bắt đầu từ câu SAU hook, không lặp hook"; parser thêm `_drop_restated_hook`: bỏ câu đầu của đoạn thân nếu giống hook ≥ 0.85 (difflib trên từ `\w+`, mức từ chứ không phải ký tự để không xoá nhầm câu chỉ cùng chủ ngữ) | Hook phải được nói đúng một lần |
| Dòng `OPEN LOOPS: none` được in sẵn trong mẫu, nên Gemini chép "none" mà không tự kiểm | `8abb508`: đổi thành `OPEN LOOPS: <mỗi câu nhắc twist/reveal/ending/surprise, kèm câu nói rõ nó là gì; hoặc none>` | Buộc model tự soát câu nhử thay vì điền mẫu |
| Gemini trích "nguyên văn" từ snippet tìm kiếm và tự bịa đường dẫn URL (3 path giả kiểu `screenrant.com/captain-america-15-...`) | `62fa7c2`: luật SNIPPET — quote chỉ thấy qua snippet phải ghi `(SNIPPET)`, đưa vào GAPS để kiểm lại; cấm viết URL chưa thấy. `576b936`: nếu kết quả chỉ hiện tên site thì ghi `(address not shown: <site>)`, không tự dựng path | Fact trace có URL giả trông như đã kiểm chứng nhưng thật ra không |

**Kết quả test lại:** hook đọc 1 lần, `OPEN LOOPS: none` đúng, kết bằng kết cục; sheet chạy lại có 0 URL bịa. 186 test Gemini/micro pass.

## 4. Đợt 3 — test end-to-end với scout You.com thật, nới luật (commit `df171af`, `86d553d`, `3fab845`)

### 4.1 Cách test

Chạy cho 3 moment thật:
- **M1:** Captain America #15 (2026), đúng ca lỗi gốc.
- **M2:** Death of Wolverine #4 (2014).
- **M3:** Superman #18 (2019).

Mỗi moment chạy trọn chuỗi bằng code thật của pipeline: planner OpenRouter → vòng general You.com → chọn ứng viên → vòng verify You.com → evidence gate OpenRouter → `build_scout_candidate` → render prompt bằng `gemini_prompt.py` → agy PHASE 1 → `WRITE` → PHASE 2 → parser của pipeline. Có 2 vòng (trước và sau khi sửa). Driver, kết quả scout, prompt, output và checker nằm ở thư mục scratchpad `e2e_youcom/` của phiên (tạm, sẽ mất khi dọn).

Giữa chừng Master yêu cầu **nới luật**: Gemini không cần trả đúng từng chữ format, miễn là nó trả lời và chạy tiếp. Vì vậy tiêu chí được chia thành hai nhóm.

Tiêu chí bắt buộc:
- **H1:** PHASE 1 luôn ra fact sheet; chỉ trả NO INFO khi không nguồn nào xác nhận hành động chính.
- **H2:** gõ `WRITE` thì chạy tiếp, parser lấy được FINAL SCRIPT, hook đọc một lần.
- **H3:** không câu nhử; biết kết cục thì kể, không biết thì viết sâu hơn khoảnh khắc chính.
- **H4:** dùng `aftermath`/context của scout khi có.
- **H5:** không bịa chi tiết.

Chỉ ghi nhận, không tính lỗi: thứ tự block, có đủ dòng audit không, quote nguyên văn hay diễn đạt lại, nhãn Tier, đếm từ.

### 4.2 Lỗi tìm thấy và cách sửa

| Commit | Lỗi | Sửa | Vì sao tổng quát |
|---|---|---|---|
| `df171af` scout | M2, M3 bị chính code loại ngay lần đầu: M2 có URL citation tự dựng từ tiêu đề trang (không nằm trong nguồn trả về); M3 có `series_issue_year` dạng "Superman (Volume 5) #18 (cover date February 2020; published December 11, 2019)" nên bị coi là không rõ issue; M1 có `aftermath` là câu nhử viết lại | Prompt scout (planner `_MICRO_INVARIANT_RULES`/`_MICRO_DETAIL_RULES` và `general_micro.v1.md`) đòi: issue dạng `Series Title #N (YYYY)` với một năm; URL chép nguyên từ trang đã truy xuất; `aftermath` để `""` khi không nguồn nào nêu, không dùng câu phản ứng làm citation | Chỉ là luật định dạng trường, không nêu tên comic nào; có test ghim |
| `86d553d` parser | Gemini bọc FINAL SCRIPT trong code fence nên dấu ``` bị đọc thành lời; dòng `Hook: ...` bị coi là option nên mất hook; nhãn `Story:` bị đọc thành lời | Fence coi như ngắt đoạn; dòng hook có nhãn là hook; bỏ nhãn `Story:/Body:/Script:/Narration:` | Không phụ thuộc nội dung truyện; Q&A giữ nguyên; 11 test mới |
| `3fab845` template | Luật quá cứng có nguy cơ làm Gemini dừng hoặc không chạy tiếp | Xem 4.3 | Không nêu tên truyện, nhân vật hay cụm từ nào |

### 4.3 Các luật đã nới (trước → sau)

- `OUTPUT EXACTLY ...` / "Return only the blocks requested" → `OUTPUT, IN ENGLISH, WITHOUT A PREFACE`; mẫu là hướng dẫn, không phải biểu mẫu để điền. Chỉ FINAL SCRIPT là bắt buộc. Giữ HOOK OPTIONS, FACT TRACE, OPEN LOOPS, UNSUPPORTED FACTS. Thành tuỳ chọn: SPOKEN WORD COUNT, OPENING CHAIN, STORY FRAME CHECK, AFTERMATH CHECK, REACTIONS.
- 4 điều kiện "NO INFO and stop" → "`NO INFO` is rare": chỉ khi hành động chính không có nguồn, bị bác bỏ, hoặc không phân biệt được issue. Scout INCONCLUSIVE, chỉ một nguồn, dưới 4 beat, chỉ có snippet, thiếu địa chỉ, thiếu frame/aftermath/context đều **không** phải lý do dừng.
- "Quote only words you actually read … beat stays unconfirmed" → ưu tiên chữ đã đọc; quote/diễn giải gắn `(SNIPPET)` vẫn dùng được, ghi vào GAPS, không bao giờ là lý do dừng.
- "Never write a URL you did not open" → dùng địa chỉ đã thấy; không có thì `(address not shown: <site>)` rồi đi tiếp. Vẫn cấm bịa path và cấm tên miền trần.
- "First open the scout URL" → mở được thì kiểm, không thì coi là manh mối và kiểm bằng tìm kiếm. "Even CONFIRMED claims must be checked" → "when you can".
- Thêm mới: PHASE 1 "Never ask me a question and never stall"; PHASE 2 "When I type WRITE, start at once: do not repeat the fact sheet and do not ask me anything".
- Trần số từ mềm hơn: hook "24 is the ceiling" → "under about 24"; "stop by 145 words" → "about 145 at most".

Phần lõi giữ nguyên và có test ghim: không câu nhử; OPEN QUESTIONS không được kể ra; kể kết cục hoặc viết sâu hơn khoảnh khắc chính; aftermath/context của scout chỉ là manh mối; fact sheet là toàn bộ sự thật được phép dùng; hook nói một lần; soát mọi câu có twist/reveal/ending/surprise.

### 4.4 Kết quả

- **Tiêu chí bắt buộc:** H1–H4 đạt ở cả 8 lượt chạy (4 chuỗi × 2 vòng). Không lần nào trả NO INFO, từ chối, hỏi ngược, hay dừng sau `WRITE`. H5 còn lỗi nhỏ, xem bên dưới.
- **Tốc độ:** sau khi nới, thời gian hai pha trung bình từ 214 s xuống 131 s (−39%); token output của pha 2 giảm 14%.
- **Scout làm đúng ở M1:** các bài review giấu twist, nên scout để `aftermath` = `""` và ghi twist vào `unrevealed`, gate trả INCONCLUSIVE, `scout_check` là NOT CONFIRMED. Gemini tự tìm ra kết cục qua kết quả tìm kiếm của nó.

Script cuối M1 (Captain America #15):
> Captain America just gave up his soul to the devil.
> Trapped in Hell, Steve Rogers leads an uprising to topple Mephisto's rule. Armed with the Antidivine, a weapon that can slay angels, he fights an army of vengeful spirits commanded by the Red Skull. Mephisto offers a deal, promising a world with no more dictatorships where democracies flourish. Steve accepts the bargain, sacrificing his soul.

**Chi tiết Steve bán linh hồn chỉ đến từ snippet tìm kiếm, chưa kiểm chứng nguyên văn: phải kiểm trước khi đăng.**

M2 (Wolverine) kể đủ tới cái chết ngạt trong adamantium. M3 (Superman) kể buổi họp báo và phản ứng của Legion of Doom; câu kết M3 hơi yếu ("react to the news") nhưng không phải câu nhử.

- **Lỗi H5 còn lại** (mỗi lượt khoảng một chỗ, Gemini vẫn tự ghi `UNSUPPORTED FACTS: none`): "ruptures", "all over him" (M2), "the devil" (M1), "contradicts his values of truth" (M3). Đây là model bỏ qua luật đã có, không phải template thiếu luật, nên không sửa thêm bằng template.
- **Lượt gọi API trả phí:**

| API | Số lượt |
|---|---:|
| Planner (OpenRouter) | 6 |
| You.com | 10 |
| Evidence gate (OpenRouter) | 2 |
| Gemini qua agy | 16 lượt gọi (8 chuỗi × 2 pha) |

- **Test:** cả nhánh 1.672 test pass; lỗi còn lại duy nhất là `tests/test_outro_card.py::test_build_outro_card_makes_clip_of_right_duration` (ffmpeg trên Mac thiếu filter, có sẵn từ trước). Không chạy test art theo yêu cầu.

## 5. Còn tồn (chưa sửa)

1. **`build_scout_candidate` lấy `evidence_urls` của gate thay cho của ứng viên**, nên URL trong `detail_citations` có thể rơi khỏi danh sách (M2). Quote của `detail_citations` cũng không được đối chiếu với nguồn (chỉ `claim_citation` được kiểm), nên quote diễn đạt lại hoặc gắn sai URL vẫn lọt. Đề xuất: gộp URL của citation vào `evidence_urls`; lọc citation có quote không khớp snippet của URL đó.
2. **Gate INCONCLUSIVE oan:** `MAX_SOURCE_CHARS = 6000` cắt trang dài; phần dự phòng chỉ xem snippet của vòng verify, không xem snippet của vòng general (nơi quote thật nằm) — ca M3.
3. Khi planner trả `extra_fields: []` thì `character` rơi về tiêu đề, còn `turning_point`/`why_it_lands` vắng mặt.
4. Parser: hook dính liền vào đoạn truyện (không có dòng trống) không được nhận là hook; hook vẫn chỉ đọc một lần nhưng scene đầu không gắn cờ intro.
5. agy headless không cho thấy URL thật (`search_web` chỉ trả tên site kèm link chuyển hướng), nên trong test mọi URL đều là của scout hoặc `(address not shown: ...)`. Gemini trên web có thể khác.

## 6. Dùng và triển khai

- Prompt writer micro: `/Users/nhanvu/Documents/code/comic-book-pipeline/prompts/gemini_micro_moment_writer.md`. Đây là template có 2 chỗ trống `<<...>>` (dòng moment và object scout). Bản đã điền cho từng comic: `projects/<tên>/<tên>_writer_prompt.md`.
- Deploy Windows (khi server vào được): `git merge --ff-only origin/feat/scout-single-selection-flow` qua WSL, chạy test, khởi động lại server detached; tạo lại `_writer_prompt.md` cho các project micro đang làm (project Steve vẫn đang dùng template đợt 1).
- Đề xuất tiếp theo từ phân tích corpus đối thủ (`COMPETITOR_CORPUS_FULL_AUDIT_2026-10-01.md`, mục 10, chưa áp):
  - nâng độ dài micro lên khoảng 110–170 từ khi sheet đủ beat;
  - hook 8–16 từ, nêu mấu chốt ngay và khớp với tiêu đề;
  - cho phép kể kết quả trước.
