# Handoff: 3 việc còn dở của Q&A scout — 2026-10-04

> **Cập nhật 2026-10-04 (working tree, chưa deploy Windows):** A và B đã được code và kiểm thử tại máy này. A có cảnh báo series trực tiếp, xác nhận riêng khi tạo project và audit override. B giữ lý do lỗi OpenRouter đã lọc trong artifact/audit và hiện banner ở lượt scout hiện tại. Mục C bên dưới là thiết kế cũ: quyết định mới nhất của Master đặt mốc `in_progress` sau khi duyệt narration ở Stage 2, rồi `produced` sau khi xác minh `final.mp4`; code này cũng đã hoàn tất tại máy này. Không chạy lại hook `in_progress` lúc vừa tạo project theo mục C cũ. Render mới dùng dấu xác minh sidecar; video cũ cần qua `ffprobe` trước khi coi là đã sản xuất. `python -m pytest -q --disable-warnings`: 2024 passed, 14 skipped; dry-run 7 ca chỉ là smoke test. Chưa nghiệm thu trên Windows server hoặc chạy live You.com.

**Trạng thái: chưa code.** Base: nhánh `feat/scout-single-selection-flow` @ `a9d3ada`. Số dòng tính ở commit này; kiểm lại trước khi sửa.

Handoff gốc là `todo/qa-scout/HANDOFF_SCOUT_HARD_RULES_AND_DEDUP_2026-10-03.md`. Codex đã làm phần lớn ở `a7ad5ce`, bộ test Mac đều qua (1775 pass). Tài liệu này chỉ gồm 3 phần chưa xong:

| # | Việc | Mục trong handoff gốc |
|---|---|---|
| A | Hiện cảnh báo đa dạng series trên UI (và chặn có xác nhận) | §3.6 |
| B | Ghi rõ lý do khi planner/gate gọi OpenRouter thất bại | §3.13 |
| C | Pipeline tự ghi sự kiện vào sổ "đã làm" (giai đoạn 2) | §4 |

Nguyên tắc chung: fix tổng quát, không gắn với comic cụ thể nào; mọi việc ghi sổ hay log không được làm hỏng pipeline (lỗi thì log và đi tiếp).

---

## A. Cảnh báo đa dạng series trên UI

**Hiện trạng**
- `stages/research_scout/workflow.py::_selected_series_detail` (~1256) tính `selected_series_count` và `series_diversity_warning`: Q&A có 3–5 mục được chọn nhưng chưa tới 3 series khác nhau.
- Kết quả chỉ được ghi vào audit trong `verify_selected` (~536) và `approve_selected` (~576).
- UI (`ui/screens/s1_research_scout.py`) **không hiện**, và không có gì chặn.
- Đã gặp ca 11/12 ứng viên Deadpool thuộc cùng một mini-series.

**Cần làm**
1. **Hàm dùng chung:** đổi `_selected_series_detail` thành hàm public, ví dụ `selected_series_detail(mode, candidate_ids, candidates)`, hoặc bọc nó trong `ui/bridge.py` thành `series_diversity(session_id, candidate_ids) -> {"count": n, "warning": bool, "series": [...]}`. Dùng lại `_series_key` hiện có, không viết parser mới.
2. **UI, chỉ mode Q&A:**
   - Mỗi lần Master tick hoặc bỏ tick một ứng viên, cập nhật một dòng cảnh báo cạnh nút Verify/Approve, ví dụ: "3 mục đã chọn chỉ thuộc 2 series — đáp án Q&A cần ít nhất 3 comic khác nhau." Liệt kê tên series.
   - Không gọi API; tính trong bộ nhớ.
3. **Chặn mềm khi Approve:** nếu `warning=True`, hiện hộp xác nhận "Vẫn tạo project?" theo đúng mẫu `override` hiện có của `create_project_from_session` (`stages/research_scout/project_factory.py:190`, cờ `override`). Bấm đồng ý thì ghi `series_diversity_override=True` vào audit.
4. Không đổi gì ở mode micro và recap.

**Test**
- Unit: 3 mục / 2 series → `warning=True`; 3 series → `False`; micro → luôn `False`; nhãn issue không parse được thì không tính là một series.
- UI smoke: dựng màn s1 với session giả, chọn 3 mục cùng series → dòng cảnh báo xuất hiện.
- Approve khi có cảnh báo mà không override → bị chặn; có override → qua, và audit ghi đúng.

---

## B. Ghi lý do khi gọi OpenRouter thất bại

**Hiện trạng**
- `stages/research_scout/planner.py::_request` (~528–552) bắt `HTTPError/URLError/TimeoutError/socket.timeout/OSError/ValueError` rồi trả `_REQUEST_FAILED`, **không giữ lý do**.
- `make_plan` (~447) trả `None`; `workflow.run_general` (~211–224) chỉ ghi `{"source": "fallback"}`.
- Hệ quả đã gặp: `.env` trên server đặt một model không tồn tại (`z-ai/glm-flash-latest`, OpenRouter trả 400 "is not a valid model ID"). Planner và evidence gate hỏng ở mọi lượt mà không ai biết, scout âm thầm dùng prompt dự phòng.

**Cần làm**
1. **Giữ lý do lỗi:** `_request` trả một đối tượng lỗi (ví dụ `RequestFailure(status: int | None, message: str)`) thay cho `_REQUEST_FAILED` trơn.
   - `message`: lấy `error.message` trong body JSON của OpenRouter nếu có, nếu không thì `str(exc)`; cắt còn ≤300 ký tự.
   - **Không bao giờ** ghi header, API key hay toàn bộ body request.
2. **Không phá caller cũ:** giữ `make_plan(...) -> ResearchPlan | None`, vì test đang inject planner theo chữ ký này. Thêm `make_plan_detailed(...) -> PlanResult(plan, error)`, hoặc lưu `last_error` trên module, và `run_general` gọi bản chi tiết khi dùng planner mặc định.
   - Lỗi parse JSON hay lỗi sau lượt repair cũng ghi lý do (`"invalid plan JSON: …"`).
3. **Workflow:**
   - `plan_record = {"source": "fallback", "planner_error": "<status> <message>"}`, ghi vào `general/plan.rev*.v1.json` và audit (`general_research_completed.detail.planner_error`).
   - Log một dòng cảnh báo qua logger/`log` hiện có.
4. **UI:** khi `plan_source == "fallback"` và có `planner_error`, hiện banner trên màn Scout: "Planner lỗi (400: … not a valid model ID) — đang dùng prompt dự phòng."
5. **Evidence gate:** áp cùng cách cho `stages/research_scout/openrouter_gate.py`. Lý do hiện chỉ là "OpenRouter request failed"; phải ghi kèm status và message (cũng ≤300 ký tự, không lộ key).
6. **Tuỳ chọn, rẻ:** lúc UI khởi động, gọi `GET /api/v1/models` (miễn phí) để kiểm `SCOUT_PLANNER_MODEL` và `SCOUT_EVIDENCE_MODEL` có tồn tại không. Không có thì log cảnh báo và hiện banner. Không chặn khởi động.

**Test**
- Giả lập HTTPError 400 với body `{"error":{"message":"x is not a valid model ID"}}` → `plan.rev1` có `source=fallback` và `planner_error` chứa "not a valid model ID".
- Không chuỗi nào trong log hay artifact chứa giá trị `OPENROUTER_API_KEY`; test bằng một key giả và quét log.
- Timeout → `planner_error` ghi "timeout".
- Gate: cùng hai ca trên.

---

## C. Pipeline tự ghi vào sổ "đã làm" (giai đoạn 2)

**Hiện trạng**
- `stages/research_scout/ledger.py` đã có:
  - `Ledger.append_event` / `append_events`;
  - `EVENT_KINDS = {proposed, in_progress, produced, published, rejected, banned, unbanned}`;
  - trường `mode, kind, key, keys, label, text, entities, scope, reason_code, refs`;
  - `_issue_key(label)` cho khoá `series-slug|năm_bắt_đầu|số_issue`;
  - `is_hard_duplicate`: chỉ micro, trạng thái `in_progress/produced/published/banned`. `rejected` **không** chặn cứng.
- **Rào ghi:** chỉ được ghi khi `os.name == "nt"` và `SCOUT_LEDGER_WRITER=windows-server`, với đường dẫn cố định `data/ledger/ledger.db`. Dòng này **đã có trong `.env` của server**, nhưng UI phải được khởi động lại mới nạp được.
- **Sổ thật trên server:** `D:\code\comic-book-pipeline\data\ledger\ledger.db` (346 sự kiện import ngày 2026-10-04) và `export.jsonl`.
- **Hiện chỉ có một đường ghi:** `scripts/ledger_maintenance.py import-legacy`, chạy tay. Workflow chỉ đọc sổ để ghi báo cáo bóng (`workflow.py` ~393, `ledger_shadow.build_shadow_report`).
- → **Không có chỗ nào tự ghi** khi tạo project, render xong, hay khi gate hoặc Master loại ứng viên.

**Cần làm**
1. **Module mới `stages/research_scout/ledger_hooks.py`:** hàm `record(event: dict) -> None`.
   - Không phải máy ghi (Mac, hoặc server thiếu biến) thì bỏ qua, chỉ log ở mức debug.
   - Bắt **mọi** exception (`LedgerWriteDisabledError`, `LedgerSchemaError`, `sqlite3.Error`, `OSError`) và log; **không bao giờ raise** vào pipeline.
   - Dùng `Ledger()` mặc định (đường dẫn cố định); id sự kiện tất định nên ghi lại hay chạy lại không tạo bản trùng.
   - Thêm helper `issue_keys_from_project(root) -> (mode, key, keys, label, text)`: đọc `comic_context.json` (micro/recap) hoặc `answer_context.json` (Q&A). Dùng lại logic của `Ledger.import_projects` cho khớp với dữ liệu đã import.
2. **Các điểm ghi:**

   | Thời điểm | Chỗ gọi | Sự kiện |
   |---|---|---|
   | Tạo project từ session scout | `project_factory.create_project_from_session` (~190), sau khi project ghi xong | `kind=in_progress`, `mode` theo session, `key` (micro/recap) hoặc `keys` (Q&A, các issue trong đáp án), `text` = câu hỏi Q&A hoặc mô tả cảnh, `refs={project, session}` |
   | Render xong video | `stages/stage_5/pipeline.py::assemble_project` (~27), **chỉ khi `final.mp4` vừa được dựng mới thành công**; bỏ qua nhánh "final.mp4 already exists" | `kind=produced`, `refs={project, final: path, rendered_at}` |
   | Gate kết luận âm | `workflow._gate_one` (~950) / `verify_selected` | `kind=rejected`, `scope=item`, `reason_code="gate_<verdict>"`. **Chỉ** với verdict âm rõ ràng (rejected/conflicting); `inconclusive` không ghi |
   | Master bỏ ứng viên khi re-scout | `rescout_keeping_confirmed` / `rescout_keeping_selected` (`dropped` ~635/653) | `kind=rejected`, `reason_code="master_turned_down"`. An toàn vì `rejected` không chặn cứng |
   | Cấm hoặc bỏ cấm bằng tay | thêm subcommand `ban` / `unban` vào `scripts/ledger_maintenance.py` (`--mode --key --reason`); UI chưa có nút cấm | `kind=banned` / `unbanned` |

   Theo quyết định của Master: không ghi `proposed` (không có cơ chế hết hạn); recap và micro độc lập; micro mỗi issue chỉ một video, nên đã `in_progress`/`produced` thì chặn cứng.
3. **Bản xuất và sao lưu:** đặt Windows Task Scheduler trên server chạy mỗi đêm `python scripts\ledger_maintenance.py backup` rồi `export`. Commit `data/ledger/export.jsonl` vào git định kỳ (git trên server chạy qua WSL), hoặc để Master commit tay. `ledger.db` và `backups/` đã được `.gitignore`.
4. **Chuyển từ chế độ bóng sang dùng thật (bước sau, khi số liệu ổn):**
   - Chạy bóng khoảng 2 tuần.
   - Đọc các `general/ledger_shadow.rev*.v1.json` để đếm chỗ sổ và cách cũ khác nhau.
   - Nếu ổn, cho bộ lọc sau (`duplicate_issue_key`) và `avoid_list` lấy dữ liệu chính từ sổ. `ledger_inventory.load_production_inventory` đã là điểm vào.

**Test**
- `record()` trên Mac (không phải máy ghi) → không ghi, không raise.
- Với guard giả cho phép ghi (kiểu `_writer_guard` của test hiện có) và DB tạm:
  - tạo project → 1 sự kiện `in_progress` đúng `key`;
  - render xong → `produced`;
  - chạy lại render khi `final.mp4` đã có → **không** thêm sự kiện;
  - gate `inconclusive` → không ghi; gate âm → `rejected`;
  - DB bị khoá hay hỏng → pipeline vẫn chạy tiếp và có dòng log.
- Micro: sau `in_progress`, `is_hard_duplicate("micro", key)` = True. Q&A: lặp issue không chặn cứng.
- Toàn bộ suite vẫn xanh.

**Nghiệm thu trên server** (sau khi deploy và khởi động lại UI):
- Tạo 1 project micro thử từ Scout → `ledger.db` có thêm `in_progress`.
- Render xong → có thêm `produced`.
- Chạy `ledger_maintenance.py export` → sự kiện mới có trong `export.jsonl`.
- Chạy lại scout cùng nhân vật → issue đó bị loại `duplicate_issue_key` (khi đã chuyển sang dùng thật), hoặc xuất hiện trong báo cáo bóng (khi còn ở chế độ bóng).

---

## Việc ngoài code
- **Khởi động lại UI server trên Windows** để nạp code `a7ad5ce` và `.env` mới (`SCOUT_LEDGER_WRITER=windows-server`, model planner/gate mặc định `deepseek/deepseek-v4-flash`).
- **Chạy nghiệm thu của handoff gốc** bằng `scripts/verify_scout_acceptance.py` trên server (6 câu hỏi, khoảng $1–2): không còn đáp án trước 2010, tỷ lệ lặp ≤ ~15%, ≥ ~2 mục mới mỗi lượt `standard`.
