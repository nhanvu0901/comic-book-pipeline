# Handoff: luật cứng đặt đầu, bỏ digest, lọc trùng cho scout You.com — 2026-10-03

**Trạng thái: chưa code.** Tài liệu này mô tả đầy đủ những gì cần đổi, ở đâu, vì sao, và kiểm thế nào. Code base: nhánh `feat/scout-single-selection-flow` @ `358b504`; Mac, origin và server Windows cùng ở commit này. Số dòng trong tài liệu tính ở commit đó; kiểm lại trước khi sửa.

Tài liệu này thay cho `TODO_QA_SCOUT_2026-10-02.md` và `DESIGN_DONE_LEDGER_2026-10-02.md` (đã gộp vào đây). Bằng chứng gốc, có số liệu và nguồn, nằm ở `todo/qa-scout/2026-10-0*.md`.

---

## 0. Danh sách việc (theo thứ tự làm)

| # | Việc | Mục |
|---|---|---|
| **1** | **Luật cứng đặt đầu prompt, bỏ danh sách "đã làm" cũ (digest 9k)** | §3.1, §3.2 |
| 2 | Danh sách tránh ≤50 dòng liên quan, do code ghép sau planner | §3.3 |
| 3 | Re-scout: loại trừ do code ghép, không đi qua planner | §3.4 |
| 4 | Bộ lọc Q&A trong code: 1 issue, năm ≥2010, không phải phim/game, trùng theo khoá issue | §3.5 |
| 5 | Đa dạng series kiểm bằng code, bỏ câu "≥3 series" khỏi prompt | §3.6 |
| 6 | Planner: "modern" = từ 2010; nhận luật cứng | §3.7 |
| 7 | Effort vòng general: `deep` → `standard`; sửa audit effort | §3.8 |
| 8 | Thêm comicbook.com và screenrant.com vào domain vòng general | §3.9 |
| 9 | Thiếu mục thì chạy bù đúng 1 lượt `standard` | §3.10 |
| 10 | Chặn input >40.000 ký tự, không log nguyên body 422 | §3.11 |
| 11 | Gắn lại URL bị model sửa (T1–T3) | §3.12 |
| 12 | Prompt dự phòng micro chỉ xin 1 ứng viên → xin nhiều cảnh | §3.13 |
| 13 | Sổ "đã làm" (SQLite trên server Windows) | §4 |
| 14 | Kiểm thử và nghiệm thu | §5 |

Việc 1–12 là **Giai đoạn 0**: không cần sổ cái, sửa ngay được phần lớn lỗi Q&A. Việc 13 là giai đoạn sau.

---

## 1. Quyết định của Master (2026-10-03)

| # | Câu hỏi | Quyết định |
|---|---|---|
| 1 | Ai ghi sổ "đã làm" | **Chỉ server Windows** |
| 2 | Micro | **Mỗi issue chỉ một video micro** |
| 3 | Recap issue X có chặn micro từ X không | **Không chặn**, hai mode độc lập |
| 4 | Ứng viên đã hiện nhưng chưa quyết có hết hạn không | **Chưa làm gì** |
| 5 | Mở rộng domain | **Có**, theo kết quả A/B (chỉ comicbook.com và screenrant.com) |
| 6 | 3–5 `standard` song song so với 1 `deep` | Đã A/B: **dùng 1 `standard`** |
| 7 | A/B 4 nhánh | Đã chạy, kết quả ở §2 |
| 8 | Chiều ô ưu tiên (nếu sau này dùng chia ô) | **Khối năm × hình thức** (one-shot, mini-series, run dài, annual) |

---

## 2. Bằng chứng (vì sao phải đổi)

### 2.1 Q&A trả ít và lặp: 10 lượt test trên server, 2026-10-02
- Mỗi lượt 2–12 nguồn. 51 ứng viên vào, 46 qua lọc, nhưng **chỉ 21 cái từ 2010 trở đi**; 20 cái trước 2010, 5 cái không có năm, câu Superman trả về phim.
- *Final Crisis #6* có mặt **5/5** lượt của câu Batman. Re-scout với "ALREADY HELD, do not propose" vẫn trả lại Batman #57 hai lần.
- Planner tự định nghĩa "modern" là "post-1986", có lần đảo nghĩa danh sách loại trừ thành "should be included".
- `build_scouted_digest()` dài đủ 11.894 ký tự nhưng bị cắt ở 9.000 nên **mất khối HARD RULES** ở cuối. Mục "project đã làm" bằng 0 vì server chỉ còn 3 project.
- Danh sách "đã làm" chiếm ~80% prompt (9.000/11.000 ký tự), phần lớn là 160 tiêu đề recap không liên quan Q&A.

### 2.2 You.com với prompt lớn: 39 lượt đo, 2026-10-03
- **Không mất ngữ cảnh**: mã kiểm tra giấu ở đầu, giữa và cuối được đọc đúng 12/12 lần, ở mọi cỡ 3k, 12k, 24k, 38k.
- Input >40.000 ký tự bị trả **lỗi 422**, không bị cắt ngầm.
- **Danh sách tránh chỉ có tác dụng một phần** (tỷ lệ ứng viên là mục đã cấm):

  | Danh sách | Tỷ lệ |
  |---|---|
  | Không có | 46% |
  | ≤50 mục | 12–15% |
  | 200 mục | 27% |
  | 800 mục | 38% |

- Đặt danh sách ở đầu hay cuối không khác nhau. `deep` không tốt hơn `standard`.
- Model tự khai "đã loại" ngay cả trong 15/15 lượt vi phạm, và `warnings` luôn rỗng. → **Không tin lời khai, code phải lọc.**

### 2.3 A/B chiến lược: 6 seed, $9,65, 2026-10-03

| Cách | Mục mới hợp lệ / lượt | Mục mới / $1 (có trích dẫn hợp lệ) | Lặp |
|---|---|---|---|
| Hiện tại (digest 9k, `deep`) | 0,75 | 5 | 62% |
| Chỉ luật cứng (`standard`) | 0,83 | 7 | 76% |
| Luật cứng + danh sách ≤50 (`standard`) | 2,0 | 33 | 14% |
| **Như trên + comicbook.com, screenrant.com…** | **3,0** | **45** | 12% |
| Như trên nhưng `deep` | 2,4 | 19 | 25% |
| 3 ô song song | 2,1 | 13 | 20% |
| Search trước rồi mới Research | 1,7 | 14 | 19% |

Những gì A/B cho thấy:
- Luật cứng nâng tỷ lệ hợp lệ từ 50% lên 96%, nhưng **không giảm lặp**. Danh sách ≤50 mới là thứ giảm lặp.
- Câu "đáp án phải ≥3 series" làm model dừng đúng ở 3 mục, tức là bị hiểu thành hạn ngạch.
- Hai lượt `standard` cùng prompt cho mục khác nhau ~79%.
- Yêu cầu "tối đa 15 mục" không làm tăng số mục: mỗi lượt chỉ 5–8 nguồn.

Toàn bộ số liệu là chỉ báo (n nhỏ).

---

## 3. Giai đoạn 0: thay đổi chi tiết

### 3.1 Việc 1a: khối LUẬT CỨNG đặt đầu prompt
**File:** `stages/research_scout/planner.py`.

**Thêm hằng mới** `_HARD_RULES = {"qa": ..., "micro": ...}`, đặt ngay trên `_INVARIANT_RULES` (~dòng 177). Đây là đúng văn bản đã chạy trong A/B v2, chỉnh nhẹ:

```text
HARD RULES — non-negotiable, they apply to EVERY candidate (candidates that break them are discarded):
1. One candidate = ONE real published comic-book ISSUE. Write `series_issue_year` exactly as
   "Series Title #N (YYYY)": series title, a single issue number, the issue's publication year.
   Movies, TV, video games, toys, multi-issue arcs and "#1-5" ranges are NOT valid candidates.
2. The issue must have been published in 2010 or later (year 2010 or higher), unless the
   question itself names another period. "Modern" means 2010 or later.
3. One candidate per distinct issue — never list the same issue twice, even from different sources.
4. Only what DID happen in a specific issue ("who did"), never hypotheticals ("who could").
5. Return EVERY qualifying issue the retrieved sources support, up to 15 — more distinct
   qualifying issues from different series are better — but never pad with weak or unsupported entries.
```

Bản **micro**:
- **Luật 1:** "One candidate = ONE scene (or one tightly connected sequence) from ONE real published comic-book ISSUE…", phần còn lại như bản Q&A.
- **Luật 2:** lấy cửa sổ năm từ `micro_recency` (đừng hard-code 2026); có `session.publication_year` thì dùng nó.
- **Luật 3:** như bản Q&A.
- **Luật 4:** "Name who acts, the concrete action or reveal, and its direct consequence, each supported by a retrieved page."
- **Luật 5:** "up to 10".

**Không** đưa câu "answer must span 3+ different comics" vào prompt (xem §3.6).

**Đổi `assemble_prompt`** (~dòng 303). Thứ tự mới:

```
[HARD RULES (theo mode)]
[_INVARIANT_RULES / _MICRO_INVARIANT_RULES]
[USER INTENT …]
[One candidate per {unit}]
[_CARDINALITY_BLOCKS[...]]
[ranking nếu có]
[_MICRO_SCOUT_RULES + _MICRO_DETAIL_RULES (micro)]
[plan.research_prompt]
[ALREADY DONE … (≤50 dòng, §3.3) — chỉ khi có]
[RECENT MICRO DEFAULT (micro, như hiện tại, workflow.py ~216)]
```

- Bỏ dòng `sections.append(f"SCOUTED DIGEST:\n{digest}")`. Thay tham số `digest` bằng `avoid_lines: Sequence[str] = ()`.
- `_CARDINALITY_BLOCKS` (~279) giữ nguyên ý "no candidate minimum, never pad". Có thể bỏ "Seek coverage from about 6 distinct source pages", vì số nguồn không do prompt quyết.
- **Unit test:** khối HARD RULES luôn là đoạn đầu tiên với mọi mode, mọi cardinality, có hoặc không có `avoid_lines`.

### 3.2 Việc 1b: bỏ danh sách "đã làm" cũ (digest)
- `stages/youcom_scout.py::build_scouted_digest` (~94–123): **không còn dán vào prompt vòng general nữa.**
  - Bỏ `digest[:9000]`. Nếu còn caller khác (CLI `youcom_scout micro/confirm/enumerate`) thì cho hàm trả list mục, không trả text bị cắt.
  - Khối "HARD RULES (these killed candidates before)" trong digest chuyển hẳn vào §3.1.
- `ui/bridge.py::_scout_workflow` (~125–131): thôi truyền `digest=build_scouted_digest()`. Truyền một nguồn danh sách tránh (§3.3), ví dụ `avoid_source=LegacyDoneList()` (giai đoạn 0) hoặc `Ledger` (giai đoạn sau).
- `stages/research_scout/workflow.py`:
  - **Đường dự phòng** (`run_general` ~199–200, `bundle.render(..., digest=self.digest)`): thay bằng `avoid=<khối ≤50 dòng>`.
  - **Các template** `research_prompts/general_*.md`, `discover_*.md`, `specific_*.md` đang có `{digest}`: đổi placeholder thành `{avoid}` (hoặc bỏ ở `specific_*`, vì vòng verify không cần danh sách tránh). Đặt khối HARD RULES ở đầu template.
  - **Các chỗ khác truyền `digest=self.digest`** (~649, ~852, ~909: discover và verify): xem từng chỗ. Discover cần danh sách tránh ngắn; verify thì không.
- Kiểm `discover_questions` (bridge ~196) vẫn chạy với danh sách tránh mới.

### 3.3 Việc 2: danh sách tránh ≤50 dòng, do code ghép
- **Mới:** `stages/research_scout/avoid_list.py`, hàm `relevant_avoid_lines(mode, user_intent, plan, extra_held=(), limit=50) -> list[str]`.
- **Nguồn dữ liệu, giai đoạn 0 (chưa có sổ):**
  - `qa_question_banlist.md` (`_banlist_lines`);
  - `comic_candidates.csv` (`_csv_lines`; chỉ dùng cho micro/recap, **không** cho Q&A);
  - `projects/*/answer_context.json` cho Q&A, cộng `projects/*/comic_context.json` / `scout_candidate` cho micro, lấy issue đã làm;
  - các mục held/turned-down của session hiện tại.
- **Nguồn dữ liệu, giai đoạn sau:** sổ cái (§4).
- **Chọn mục:**
  - Tách theo mode: Q&A dùng câu hỏi và các issue trong đáp án; micro dùng issue.
  - Xếp theo độ trùng từ với thực thể trong câu hỏi và plan (dùng `utils.lexical_sim.token_jaccard` hoặc đếm token tên nhân vật/series); mục đúng nhân vật luôn lên trước.
  - Lấy tối đa **50**.
- **Định dạng mỗi dòng:** `Series Title #N (YYYY)`. Khoảng issue thì tách ra từng dòng (đã gặp `Wolverine #21-25`).
- **Tiêu đề khối:** "ALREADY DONE — do not return these issues. They have already been used; every candidate you return must be a DIFFERENT issue from all of these:".
- **Vị trí:** sau `research_prompt` (§3.1). Vị trí không ảnh hưởng đo được.
- **Không bao giờ cắt ngầm:** vượt ngân sách ký tự thì log số dòng bị bỏ và chỉ giữ 50.

### 3.4 Việc 3: re-scout loại trừ bằng code
- **Hiện tại:** `workflow.py::_rescout_note` (~933) biến held/turned-down thành *feedback note*. Note đó đi vào `planner.make_plan` (`_user_message` ~454), và planner viết lại làm mất hoặc đảo nghĩa.
- **Đổi:**
  - Vẫn giữ note cho planner, để planner biết ngữ cảnh.
  - **Thêm:** `run_general` lấy các khoá của mục held, turned-down và đã chọn trong session, rồi đưa vào `relevant_avoid_lines(..., extra_held=...)`. Các khoá này luôn đứng đầu 50 dòng.
  - Đồng thời truyền chúng thành `protected_keys` cho bộ lọc sau (§3.5), để mục trùng bị loại bằng code dù model có trả lại.
- **Unit test:** re-scout giữ Batman #57; lượt mới trả Batman #57 từ một URL khác thì bị loại với lý do `duplicate_issue_key`.

### 3.5 Việc 4: bộ lọc Q&A trong code
**File:** `workflow.py::_validate_new_general_candidates` (~1085). Hiện micro có `micro_issue_rejection_reason` và `micro_release_rejection_reason`, còn Q&A không có gì.

**Thêm cho Q&A**, chạy sau kiểm citation; lý do được ghi vào `candidate_validation.rev*.json`:

1. **`qa_issue_unparsed`**: `series_issue_year` không parse được thành đúng 1 issue (dùng parser có sẵn: `issue_identity` / `_ISSUE_RE`); dải `#1-5` cũng tính là lỗi.
2. **`qa_pre_2010`**: năm < 2010, trừ khi câu hỏi tự nêu thời kỳ khác (tái dùng logic nhận diện năm/thời kỳ trong ý định của `micro_recency`, hoặc `session.publication_year` nếu có).
3. **`qa_not_comic`**: chứa từ khoá phim/TV/game (movie, film, season, episode, video game, animated series…), hoặc không có `#N`.
4. **`duplicate_issue_key`**: khoá issue = series chuẩn hoá (`_normal_series`; bỏ "the", hậu tố `(Vol. N)`, `(YYYY)`) + số issue, **bỏ năm** vì model hay ghi sai năm. Trùng với khoá đã nhận trong lượt này, với `protected_keys` (§3.4), hoặc với danh sách đã làm (§3.3) thì loại.
   - **Không bao giờ so mờ số issue:** "Venom #13" và "Venom #1" cho `token_set_ratio` 96,8.

Bộ lọc **micro** giữ nguyên, và thêm `duplicate_issue_key` để thực thi quyết định 2 (mỗi issue một micro).

### 3.6 Việc 5: đa dạng series kiểm bằng code
- Bỏ "Answer must span 3+ DIFFERENT comics" khỏi mọi prompt, vì model hiểu thành hạn ngạch và dừng ở 3 mục.
- **Bước review:** UI Scout (`ui/screens/s1_research_scout.py`) và `verify_selected`/`approve_selected` (workflow ~319, ~422) cảnh báo hoặc chặn khi 3–5 mục được chọn đến từ < 3 series khác nhau.
- **Vòng general:** nếu toàn bộ ứng viên hợp lệ chỉ thuộc 1–2 series, đánh dấu "thiếu đa dạng" để kích hoạt lượt bù (§3.10). Ca đã gặp: câu Deadpool ra 11/12 mục từ cùng một mini-series.

### 3.7 Việc 6: planner
- `_SYSTEM_PROMPT` (~364): thêm câu "'Modern' means published 2010 or later; never redefine it as post-1985/post-Crisis."
- **Giữ** luật "NEVER narrow a scope the Master did not narrow", nhưng ghi rõ năm 2010+ là chính sách kênh, không phải thu hẹp phạm vi.
- `_user_message` (~454): với Q&A, thêm dòng nhắc chính sách năm, giống cách micro đã thêm `recent_micro_instruction()`.
- Không đưa danh sách loại trừ cho planner viết lại; chỉ code ghép (§3.4).

### 3.8 Việc 7: effort và audit
- `config.py:46`: `YOUCOM_GENERAL_EFFORT` mặc định `"deep"` → `"standard"`. Kiểm `.env` trên server không ghi đè.
- `workflow.py:306`: audit đang ghi `config.YOUCOM_RESEARCH_EFFORT` ("standard") trong khi vòng general chạy `YOUCOM_GENERAL_EFFORT`. Sửa thành `config.YOUCOM_GENERAL_EFFORT`, và ghi đúng effort của discover/verify ở các chỗ tương ứng.
- Giữ `YOUCOM_VERIFY_EFFORT` như hiện tại; chưa có bằng chứng để đổi.

### 3.9 Việc 8: domain
- `research_policies/source_profiles.v1.json` → `general_research.domains`: **thêm `comicbook.com` và `screenrant.com`**. Không thêm gamerant, popverse, image.fandom, wikipedia: A/B không thấy đóng góp nào.
- Tạo bản policy mới (ví dụ `source_profiles.v2.json`) nếu repo đang đánh version policy, để audit biết lượt nào dùng danh sách nào.
- `specific_web_search` (vòng verify) để nguyên.
- `stages/youcom_scout.py:41 DOMAINS` (CLI cũ): cập nhật cho đồng bộ.

### 3.10 Việc 9: thiếu mục thì chạy bù 1 lượt
- Sau `run_general`, nếu số mục mới hợp lệ < ngưỡng (Q&A: 5; micro: 3) **hoặc** thiếu đa dạng series (§3.6), thì chạy **thêm 1 lượt `standard` cùng prompt**.
  - Thêm vào danh sách tránh các khoá vừa nhận và vừa va chạm (chúng được ưu tiên trong 50 dòng).
  - Gộp kết quả theo khoá issue.
- **Tối đa 1 lượt bù.** Sau đó báo "lane cạn" nếu vẫn thiếu.
- Chi phí thêm $0.05. A/B cho thấy hai lượt cùng prompt khác nhau ~79%.
- Thêm cờ cấu hình `SCOUT_TOPUP_ROUNDS=1`.
- **Chưa** làm chạy song song theo ô; chỉ cân nhắc khi danh sách đã bão hoà.

### 3.11 Việc 10: chốt độ dài input, log an toàn
- `stages/research_scout/youcom.py::research` (~47–56): nếu `len(prompt) > 40_000` (đếm ký tự, không phải byte) thì raise lỗi rõ ràng trước khi gọi, kèm số ký tự.
- `RawCall.response_text` (~29) và mọi chỗ log body lỗi: body 422 của You.com lặp lại **nguyên cả prompt**. Cắt còn khoảng 2.000 ký tự khi log hoặc lưu.

### 3.12 Việc 11: gắn lại URL bị model sửa
- **Hiện tại:** khoảng 20–25% ứng viên bị loại `claim_citation_url_not_returned` vì model viết lại slug, thêm `www.`, hoặc đổi `Vol_1_2` thành `Vol_1_1`.
- **Mới:** trong `cited_sources.py`, thêm `match_key(url)` (để `canonical_url` ~114 nguyên cho các chỗ khác):
  - host chữ thường, bỏ `www.`/`m.`/`amp.`, bỏ scheme và cổng mặc định;
  - path giải mã percent, bỏ `/` cuối;
  - bỏ query theo dõi (utm, fbclid, gclid…);
  - tiêu đề MediaWiki: `_` ≡ khoảng trắng.
- **Gắn lại theo bậc**, trong `_validate_new_general_candidates` trước khi loại; dừng ở bậc đầu tiên khớp:
  - **T1:** `match_key(model_url) == match_key(returned_url)`.
  - **T2:** cùng host và cùng khoá cấu trúc: fandom `Series_Vol_N_ISSUE`; leagueofcomicgeeks `/comic/{id}/`; batcave `{id}-slug`.
  - **T3:** câu trích (đã chuẩn hoá) nằm trong snippet của **đúng một** nguồn trả về.
- **Chặn cứng:** các con số trong hai URL (issue, vol, năm) khác nhau thì không gắn lại bằng độ giống slug; chỉ T3 được quyết.
- Ghi `rebound_from` và `rebound_reason` lên ứng viên. Gate phía sau vẫn tải trang trích dẫn để kiểm (lưới thứ hai).

### 3.13 Việc 12: prompt dự phòng micro
- `research_prompts/general_micro.v1.md` viết "The candidate is ONE scene…", "there is no candidate minimum". Khi planner hỏng (như lỗi model OpenRouter trên server trước đây), You.com chỉ trả **1 ứng viên**.
- Đổi thành xin **tối đa ~10 cảnh khác nhau**, mỗi cảnh một issue, không độn. Tạo bản `general_micro.v2.md` và cập nhật policy bundle.
- Thêm log rõ lý do khi `make_plan` trả `None` (hiện im lặng): ghi HTTP status và message của OpenRouter, không ghi key.

---

## 4. Giai đoạn sau: sổ "đã làm" (việc 13)

Theo quyết định 1, **chỉ server Windows ghi**.

- **Nguồn sự thật:** SQLite `data/ledger/ledger.db` trên server.
  - Chế độ WAL; mọi ghi đi qua một hàm duy nhất có khoá.
  - Không đặt file trong thư mục Google Drive.
  - Sao lưu bằng `VACUUM INTO` hằng đêm.
- **Bản xuất:** `data/ledger/export.jsonl`, xuất định kỳ và commit vào git. Mac chỉ đọc bản này.
- **Bảng `events`:** `id` (hash tất định, để ghi lại hay import lại không bị trùng), `ts`, `mode` (micro/qa/recap), `kind` (proposed/in_progress/produced/published/rejected/banned/unbanned), `key` (khoá issue `series_slug|năm_bắt_đầu_series|số_issue`), `keys` (Q&A: các issue trong đáp án), `label`, `text` (câu hỏi/cảnh), `entities`, `scope` (item/lane), `reason_code`, `refs` (project, session, batcave id, fandom slug, URL).
- **Trạng thái của một khoá:** banned > produced/published > in_progress > rejected > proposed. **Không có hết hạn** (quyết định 4).
- **Khi nào ghi:**
  - hiện ứng viên cho Master: `proposed`;
  - gate loại: `rejected`;
  - Master loại hoặc cấm: `rejected` / `banned`;
  - tạo project (`project_factory`): `in_progress`;
  - xong narration/render/đăng: `produced` / `published`.
- **Import một lần, chạy lại an toàn:** banlist, `comic_candidates.csv`, `qa_question_bank.md`, `projects/*`. Từ đó **dọn `projects/` không còn làm mất trí nhớ**.
- **Đường đọc:**
  - `relevant_avoid_lines` (§3.3) lấy từ sổ;
  - bộ lọc sau (§3.5) tra khoá trong sổ;
  - Q&A còn so **tập issue của đáp án**: trùng ≥2/3 issue, hoặc giao/tập nhỏ ≥0,5, là cùng một video.
- **Luật theo mode:**
  - micro: trùng issue là loại cứng (quyết định 2);
  - recap và micro độc lập với nhau (quyết định 3);
  - Q&A: một issue lặp trong nhiều câu hỏi chỉ là tín hiệu mềm.
- **Định danh theo bậc:**
  1. id ngoài (batcave, fandom slug);
  2. khoá chuẩn;
  3. bảng alias (`aliases.json`);
  4. so mờ tên series chỉ khi số issue bằng nhau (Jaccard ≥0,8 là cùng; 0,5–0,8 nhờ gate xử; <0,5 là khác). Ngưỡng cần hiệu chỉnh trên dữ liệu thật.
- **Lộ trình:**
  1. `ledger.py` + import + **chế độ bóng**: tính quyết định của sổ song song với cách cũ, chỉ ghi log khác biệt;
  2. chuyển đường đọc sang sổ;
  3. alias, gate cho ca mơ hồ, khoá có cấu trúc trong `output_schema` (`series`, `issue_number`, `year` thành trường riêng).
- **Lựa chọn sau, chỉ khi số liệu cho thấy cần:** chia ô theo khối năm × hình thức (quyết định 8) khi danh sách tránh đã bão hoà.

---

## 5. Kiểm thử và nghiệm thu

**Unit test mới (trong sandbox, không gọi mạng):**
- Prompt luôn bắt đầu bằng HARD RULES, với mọi mode, cardinality, có/không plan, có/không danh sách tránh.
- Không còn chuỗi "SCOUTED DIGEST" trong prompt vòng general; prompt ≤ ~6k ký tự với 50 dòng tránh.
- `relevant_avoid_lines`:
  - tối đa 50 dòng, mục đúng nhân vật lên trước;
  - Q&A không nhận tiêu đề recap;
  - khoảng issue được tách ra.
- Bộ lọc Q&A loại được: năm 1988; "Superman Returns"; "Wolverine #21-25"; cùng issue trích từ 2 URL; issue trùng `protected_keys`.
- Re-scout: mục held bị trả lại thì bị loại `duplicate_issue_key`.
- Input 40.001 ký tự raise lỗi trước khi gọi API.
- Gắn lại URL: `www.` và `/` cuối là T1; slug khác nhưng cùng `Vol_N_ISSUE` là T2; `Vol_1_2` và `Vol_1_1` **không** được gắn lại.
- Audit ghi đúng effort.

**Nghiệm thu thật** (trên server Windows, sau khi deploy, khoảng $1–2):
- Chạy lại 6 seed của đợt A/B (4 Q&A, 2 micro) và câu Batman ở chế độ re-scout.
- **Đạt khi:**
  - 0 ứng viên trước 2010 hoặc không phải comic lọt qua;
  - *Final Crisis #6* và Batman #57 không quay lại sau khi đã held;
  - tỷ lệ lặp ≤ ~15%;
  - mục mới hợp lệ ≥ ~2 mỗi lượt `standard`;
  - không có prompt nào thiếu HARD RULES.
- Giữ bộ test toàn repo xanh (trên Mac hiện chỉ có 1 lỗi ffmpeg có sẵn: `tests/test_outro_card.py::test_build_outro_card_makes_clip_of_right_duration`).

---

## 6. Rủi ro và điều chưa chắc
- Số liệu A/B và test prompt lớn có n nhỏ (1–3 lượt mỗi ô, 6 seed). Hướng đi rõ, nhưng con số tuyệt đối còn dao động.
- Tập "đã làm" lúc test chỉ 5–33 mục; khi sổ thật lớn (50–200 mục liên quan), cần đo lại độ dài danh sách tối ưu.
- Năm và series do model tự khai. Bộ lọc dựa trên trường đó, còn gate kiểm sau.
- Chính sách năm 2010+ cho Q&A cần ngoại lệ khi câu hỏi tự nêu thời kỳ cũ; phải test kỹ phần nhận diện này.
- Chưa xác minh trên hoá đơn xem lượt bị lỗi 422 có tính tiền không.

## 7. Việc ngoài code
- **Khởi động lại UI server** trên Windows để nạp `.env` mới (đã xoá `SCOUT_PLANNER_MODEL` / `SCOUT_EVIDENCE_MODEL` không hợp lệ; planner đã trả HTTP 200 với `deepseek/deepseek-v4-flash`). UI đang chạy từ trước khi sửa `.env` nên vẫn dùng cấu hình cũ.
- Commit tài liệu này và `todo/qa-scout/`.

## 8. Tham chiếu
- `todo/qa-scout/2026-10-02-done-memory-architecture.md`: kiến trúc bộ nhớ, lớp lưu trữ, định danh comic.
- `todo/qa-scout/2026-10-02-youcom-dedup-and-exclusion.md`: You.com API, giá, lọc trùng, gắn lại URL.
- `todo/qa-scout/2026-10-02-context-loading-papers.md`: paper về nạp context cho agent tìm kiếm.
- `todo/qa-scout/2026-10-02-novelty-search-papers.md`: paper về tìm kiếm hướng tới cái mới và chia ô.
- `todo/qa-scout/2026-10-03-youcom-large-prompt-test.md`: đo prompt lớn và danh sách tránh.
- `todo/qa-scout/2026-10-03-scout-strategy-ab-test.md`: A/B chiến lược scout.
