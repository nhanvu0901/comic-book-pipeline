# Handoff: corpus đối thủ, micro-moment scout và Gemini writer

**Snapshot dữ liệu: 2026-10-01 14:06 UTC (11:06 ADT).** Worker phiên âm đang chạy nên số SRT có thể tăng sau mốc này. Tài liệu ghi lại dữ liệu thực đã có, cách rút quy tắc viết/scout, những thay đổi đã đưa vào pipeline và phần việc chưa hoàn tất. Đây là handoff cho người tiếp tục phân tích hoặc sản xuất, không phải lời hứa đạt mức view/retention của đối thủ.

## 1. Trạng thái dữ liệu hiện tại

Nguồn âm thanh: [`research/competitor_audio_2026-09-28/`](research/competitor_audio_2026-09-28/). Các MP3/SRT là dữ liệu local, git-ignored; tài liệu, prompt và code được theo dõi trong repo. `yt-dlp` liệt kê 3.815 Shorts từ năm kênh, tải công khai được **3.808 MP3**. Bảy video cần xác nhận tuổi bằng tài khoản; ID nằm trong [README của kho âm thanh](research/competitor_audio_2026-09-28/README.md).

| Kênh | MP3 | SRT hiện có | Còn thiếu SRT |
| --- | ---: | ---: | ---: |
| Flikey | 132 | 132 | 0 |
| ComicsUnlocked | 1.319 | 1.319 | 0 |
| ComicCandid | 860 | 666 | 194 |
| Comic_Escape | 1.095 | 6 | 1.089 |
| comiczyt | 402 | 7 | 395 |
| **Tổng** | **3.808** | **2.130** | **1.678** |

Con số trên được đếm trực tiếp từ file và đối chiếu [`transcription_manifest.tsv`](research/competitor_audio_2026-09-28/transcription_manifest.tsv): **1.904 `DONE`, 226 `DONE_EXISTING`, 1.678 `PENDING`, 0 `FAILED`** tại snapshot. [`transcription_qc.tsv`](research/competitor_audio_2026-09-28/transcription_qc.tsv) có 41 dòng kiểm tra, đều `ACCEPT`. Mục tiêu QC của [handoff phiên âm cũ](HANDOFF_TRANSCRIBE_ALL_COMPETITOR_MP3.md) là ít nhất 50 mẫu, tối thiểu 5 mỗi kênh; bước này chưa xong. Handoff phiên âm cũ ghi 2.034/1.774 tại thời điểm 2026-09-30. **Đọc manifest để lấy số mới khi worker đang chạy; chỉ chạy dry-run sau khi worker dừng.**

Các phân tích đã dùng **hai tập khác nhau**:

1. [Audit script](COMPETITOR_SCRIPT_AUDIT_2026-09-28.md) và [audit chọn moment](MICRO_MOMENT_REFERENCE_AUDIT_2026-09-30.md) đọc **34 SRT được chọn có chủ đích**: video nhiều view, video gần đây và video ở giữa mỗi kênh. Đây là đọc sâu định tính; không đại diện thống kê cho 3.808 video.
2. [Audit hook](HOOK_OPENING_AUDIT_2026-09-30.md) ghép **2.034 SRT** với ID, tiêu đề và snapshot view công khai; so sánh quartile view **trong từng kênh**. Lúc đó ComicsUnlocked 1.319, ComicCandid 570, Flikey 132, Comic_Escape 6, comiczyt 7. Đến snapshot này có thêm 96 SRT của ComicCandid; **96 bản này chưa được đưa vào số liệu hook**. Hai kênh cuối hiện quá ít transcript để suy rộng định lượng.

MP3 không tự cho biết retention, tỷ lệ *viewed versus swiped*, upload date đáng tin cậy hay tác động riêng của lời kể. View là một snapshot, chịu ảnh hưởng của chủ đề, tuổi video, phân phối, hình ảnh, giọng đọc và quy mô kênh. Một “conversion point” nêu dưới đây là **điểm xoay của câu chuyện trong lời kể**, không phải thời điểm người xem thật quyết định ở lại.

## 2. Điều học được từ năm kênh

Các ví dụ và timestamp có URL trong hai audit ở trên. Tóm tắt này chỉ giữ phần có thể chuyển sang Short micro 15–50 giây:

| Kênh | Điểm mạnh quan sát được | Cách dùng trong micro-moment |
| --- | --- | --- |
| **Flikey** | Một hành động nhỏ của nhân vật quen thuộc đổi nghĩa khi hệ quả lớn lộ ra; ví dụ Thanos giúp người phụ nữ rồi sự trì hoãn làm đổi tương lai của cô ấy. | Tìm **hành động → hệ quả trái kỳ vọng** trong cùng chuỗi cảnh, giữ đúng nguyên nhân được nguồn chứng minh. |
| **ComicsUnlocked** | Câu hỏi/đối đầu dễ hiểu, sau đó lộ đòn phản công hoặc chuẩn bị sẵn; ví dụ Spider-Man tưởng bị Avengers bắt nhưng nói đã chuyển thiết bị đi. | Tìm **thế bất lợi → hành động cụ thể đảo thế**. Nếu video gốc là list/ability explainer, chỉ lấy một cảnh tự đứng được. |
| **ComicCandid** | Quan hệ nhân vật hoặc người giúp bất ngờ khiến một quyết định có rủi ro và kết quả cá nhân rõ; ví dụ Lex giúp Barbara. | Chấp nhận lựa chọn yên lặng và đối thoại khi chính quyết định cùng kết quả được nguồn xác nhận; không bắt buộc cảnh đánh nhau. |
| **Comic_Escape** | Tựa và nội dung thường neo vào một lời thú nhận, cái chết, sự trở lại hoặc hậu quả cảm xúc. | Tìm **lời nói/quyết định thay đổi cách hiểu hành động**; rút một đoạn tự đủ khỏi recap hoặc list dài. Nhận xét này dựa chủ yếu vào metadata và mẫu ít SRT. |
| **comiczyt** | Tiền đề hành động rất lạ và cú lật thứ hai; ví dụ Batman/Penguin với bí mật danh tính. | Dùng **một cuộc đối mặt và một phản tiết lộ**; không bê nguyên kịch bản khoảng 100 giây. Nhận xét này cũng chỉ dựa trên mẫu SRT nhỏ. |

Các “điểm xoay” được hand-check tại timestamp trong [audit moment](MICRO_MOMENT_REFERENCE_AUDIT_2026-09-30.md): Thanos khoảng 0:08–0:13 rồi hệ quả khoảng 0:28; Spider-Man khoảng 0:29–0:37; Lex/Barbara khoảng 0:35–0:38; comiczyt/Batman khoảng 1:20 trong video dài hơn. Chúng mô tả **thời điểm lời kể đổi nghĩa cảnh**, không chứng minh chính giây đó làm view tăng.

Trong 2.034 transcript của [audit hook](HOOK_OPENING_AUDIT_2026-09-30.md), first cue trung vị dài 14 từ và 4,16 giây, nhưng ranh giới cue do ASR tạo không phải “cửa sổ retention”. Top/bottom quartile trong cùng kênh có độ dài câu mở đầu khác nhau: ComicsUnlocked 9/16 từ, ComicCandid 18/14, Flikey 11/14. ComicsUnlocked dùng câu hỏi mở đầu nhiều hơn trong top quartile, ComicCandid thì ngược lại. Vì vậy đã bỏ quy tắc phổ quát kiểu “không được hỏi”, “tối đa 12 từ” hay “tên phải ở ba từ đầu”. Tín hiệu mô tả có ích hơn là **hook hứa một người + sự việc cụ thể → câu kế tiếp đưa một fact khác → câu thứ ba tạo chuyển động về lời giải**. Không có bằng chứng nhân quả rằng công thức này tự làm tăng view.

## 3. Scout micro-moment đã đổi như thế nào, vì sao

### Đơn vị nội dung và chất lượng cảnh

Trước đây scout dễ trả về một “feat” hoặc nhận định trừu tượng như “đấu trí”, không có hành động nào thật sự đổi cục diện. [Audit moment](MICRO_MOMENT_REFERENCE_AUDIT_2026-09-30.md) cho thấy các ví dụ tốt khác nhau về bề mặt nhưng có chung cấu trúc: **bối cảnh dễ hiểu → hành động/quyết định/tiết lộ cụ thể → hệ quả trực tiếp**. Đó là một cảnh hoặc chuỗi sát nhau trong **một issue**, không phải cả arc. Vì vậy:

- [`research_policies/general_angles.v1.json`](research_policies/general_angles.v1.json) đổi năm góc tìm kiếm sang hành động nhỏ có hệ quả lớn, thất bại che giấu chuẩn bị, đối thủ giúp đỡ, lựa chọn/lời thú nhận làm đổi đối đầu và tiết lộ đảo lợi thế. Đây là *search leads*, không phải năm công thức bắt buộc.
- [`research_prompts/discover_micro.v2.md`](research_prompts/discover_micro.v2.md) yêu cầu `moment` có setup, turn, consequence; `turning_point` nêu ai làm gì; `what_visibly_happens` đủ để tìm cảnh; `why_it_lands` giải thích sự thay đổi. Không nhận một nhãn như “tactical patience” thay cho hành động.
- [`research_prompts/general_micro.v1.md`](research_prompts/general_micro.v1.md) yêu cầu tóm tắt 2–3 câu của **cùng cảnh**, citation có URL + câu trích dẫn nguyên văn cho sự kiện quyết định. [`research_prompts/specific_micro.v2.md`](research_prompts/specific_micro.v2.md) kiểm lại setup, hành động và hệ quả, comic hay adaptation, đúng issue hay issue khác.
- [`stages/research_scout/planner.py`](stages/research_scout/planner.py) thêm invariant micro ở **nhánh planner**. Nếu planner tự viết prompt quá chung, quy tắc một cảnh, nguồn thật và turn cụ thể vẫn được ghép vào. Q&A không dùng các invariant micro này.
- [`stages/research_scout/workflow.py`](stages/research_scout/workflow.py) chỉ đưa discovery candidate ra UI khi có năm issue rõ, turning point, diễn biến, lý do nó “land” và URL nguồn; general round vẫn kiểm citation gắn với nguồn thật. Exact-issue gate giữ yêu cầu người dùng không bị lái sang comic khác. [`stages/youcom_scout.py`](stages/youcom_scout.py) dùng cùng hướng chọn moment cho CLI.

**Vì sao không bắt buộc một “broken constant”, trận đánh lớn, hai tên nổi tiếng hoặc câu đùa?** Năm kênh có nhiều kiểu scene; cả lựa chọn nhỏ và lời thú nhận đều có thể tạo cú lật. Những yếu tố trên chỉ giúp xếp hạng khi sự kiện cụ thể đã được xác minh. Đồng thời không lấy transcript đối thủ làm bằng chứng rằng comic gốc thật sự diễn ra như thế: transcript chỉ là lead để đi tìm nguồn comic/review.

### Sửa lỗi scout chỉ đề xuất comic cũ

Một bản thay đổi trước đó đã cho phép tìm “recent and evergreen”, rồi Stage 1 không có gate năm phát hành; một issue 2014 đứng trước issue 2026 vẫn lọt vào shortlist. Quy tắc hiện tại ở [`stages/research_scout/micro_recency.py`](stages/research_scout/micro_recency.py): với yêu cầu micro **mở**, lấy issue đã xuất bản **năm hiện tại trước, năm trước sau** (ngày chốt tài liệu là 2026 rồi 2025). Năm được đọc **sau `#issue`**, vì `Series (2022) #67 (2026)` có năm mở series khác năm phát hành issue. Năm mơ hồ/thiếu hoặc ở tương lai bị loại; yêu cầu đích danh issue, năm cũ hay thời kỳ cũ được giữ. Code lọc ứng viên trong discovery, general và CLI; trước khi cắt xuống `count`, ứng viên năm hiện tại được xếp trên năm trước. Ứng viên cũ được “keep” từ vòng rescout cũng qua gate này.

Các prompt và hướng dẫn agent scout đã đồng bộ với mặc định mới: [`discover_micro.v2.md`](research_prompts/discover_micro.v2.md), [`general_micro.v1.md`](research_prompts/general_micro.v1.md), [`moment-scout.md`](.claude/agents/moment-scout.md). **Lưu ý lịch sử:** mục “search recent and evergreen; recency chỉ là bonus” trong [audit moment ngày 2026-09-30](MICRO_MOMENT_REFERENCE_AUDIT_2026-09-30.md) phản ánh quyết định **trước** lần sửa lỗi recency; chính sách hiện hành ở code/prompt mới có quyền ưu tiên. Đây là thay đổi theo yêu cầu người dùng về truyện mới, không phải kết luận thống kê rằng truyện mới luôn nhiều view hơn.

## 4. Gemini writer đã đổi như thế nào, vì sao

### Prompt micro chạy hai pha

[`prompts/gemini_micro_moment_writer.md`](prompts/gemini_micro_moment_writer.md) là template hiện hành được [`stages/stage_3/gemini_prompt.py`](stages/stage_3/gemini_prompt.py) chọn khi `pipeline_mode=micro_moment`. Pha 1 tìm fact sheet; chỉ khi người dùng gõ `WRITE` mới chạy pha 2. Những thay đổi chính:

1. **Scouting không đồng nghĩa đã đủ fact để kể.** Prompt mở URL scout, kiểm quote, series/volume/issue và tìm thêm nguồn cho hành động chính. `INCONCLUSIVE` là tín hiệu nghiên cứu tiếp, không tự động `NO INFO`; `NO INFO` khi hành động không có nguồn Tier 1/2, nguồn mâu thuẫn không gỡ được, issue không xác định, hoặc mọi cách kể đều cần bịa quan hệ nhân quả. Không còn quota cứng bốn beat/hai review khi một nguồn mạnh đã đủ cho một Short ngắn. Lý do: prompt cũ biến nguồn mỏng thành `NO INFO` hoặc ép model lấp khoảng trống cho đủ 45 giây.
2. **Tìm “story frame” thật, không thêm lore.** Fact sheet ghi tình huống/mục tiêu trực tiếp dẫn đến cảnh, với B-ID và nguồn riêng; một thái độ chung (“Batman không tin người có sức mạnh”) không thay thế được tình huống đang diễn ra. Nếu không có nguồn thì `UNKNOWN`. Đây là sửa theo lỗi kịch bản đã giải thích mánh khóe nhưng thiếu lý do hai nhân vật gặp nhau; quy tắc áp dụng cho mọi comic, không hardcode cảnh mẫu. Prompt nội bộ [`stages/stage_3/micro_moment.py`](stages/stage_3/micro_moment.py) được đồng bộ để dùng một frame clause sớm khi nguồn/ordered beats có hỗ trợ, **thay cho câu lặp**, không tăng độ dài.
3. **Hook theo dữ liệu, không theo khẩu hiệu.** Tạo ba lựa chọn: sự kiện lạ, setup ngắn, hoặc câu hỏi cụ thể; chọn cái mà nguồn trả được. Hook phải khớp promise của MOMENT/title, thường 6–15 từ, trần 24 do importer; không buộc tên ở ba từ đầu, không buộc mở bằng statement, không bắt reveal hết payoff. Câu kế đưa một fact mới; câu thứ ba đưa chuyển động. Lý do: audit 2.034 SRT không ủng hộ một cú pháp hook duy nhất, nhưng các ví dụ mạnh thường có chuỗi mở đầu cụ thể và không vòng lại cùng ý.
4. **Comic tự tạo độ hài.** Bỏ quota hai joke, meme slang, câu chốt “tie-back” và outro cưỡng ép. Mặc định không thêm narrator joke; nếu có một quan sát ngắn, nó phải bám fact được xác minh. Điều này giữ voiceover giống người kể cho bạn nghe hơn và tránh cliche/lặp công thức.
5. **Độ dài theo số fact.** Một scene đơn giản thường 35–90 từ; có thêm sự kiện độc lập có thể dài hơn nhưng tối đa 145 từ, không có mức tối thiểu. Kết ở hiệu ứng/chi tiết mạnh nhất đã được nguồn chứng minh. Lý do: nguồn chỉ có một action không thể mang 120–170 từ trung thực; ép dài tạo câu lặp hoặc choreography giả. Thời lượng vẫn phải đo bằng TTS/render thật.
6. **Audit từng mệnh đề.** Hook, câu cuối, phủ định (“không”, “chưa từng”), counterfactual (“would have”), động cơ, quan hệ và từ nối nhân quả đều cần B/R-ID. Một điều kiện bị lỡ không tự chứng minh sự kiện đó đã xảy ra hoặc không xảy ra. Pha 2 in `FACT TRACE`, `OPENING CHAIN`, `STORY FRAME CHECK`, word count và phần unsupported để người làm video kiểm lại với quote.

Prompt Q&A [`prompts/gemini_qa_writer_template.md`](prompts/gemini_qa_writer_template.md) cũng được cập nhật trong đợt phân tích script/hook trước: hook nhiều item phải nêu **chủ đề danh sách + ví dụ lạ ở item 1**, giữ đúng thứ tự item, các đoạn khác nhau ở điểm vào, không gắn tie-back/outro bắt buộc, và khoảng 110–160 từ khi nguồn đủ. **Lần sửa recency micro sau đó không đổi logic scout Q&A.**

### Kiểm tra đã làm và giới hạn

- [Audit script](COMPETITOR_SCRIPT_AUDIT_2026-09-28.md) ghi các probe AGY/Gemini: Q&A ba item trên Gemini 3.1 Pro ra 129 từ, Flash có sẵn trong CLI là **Gemini 3.8 Flash** ra 127 từ; fixture Thanos trên Pro ra 107 từ và giữ counterfactual đúng khi nguồn đủ. CLI lúc đó **không có Gemini 3.5 Flash**, nên không được nói 3.5 Flash đã test. Các probe dùng fact sheet giả hoặc lấy từ transcript, tắt search: chúng kiểm pha viết và source obedience, **không xác minh comic thật**.
- Một probe fact sheet quá mỏng vẫn làm model suy diễn sự kiện; sau khi bổ sung cấm biến điều kiện thành sự kiện, probe kế tiếp trả `NO INFO: NEED MORE VERIFIED BEATS`. Điều này là tín hiệu cải thiện, **không chứng minh model luôn tuân thủ**; người sản xuất phải đối chiếu từng câu với quote.
- Lần sửa recency chạy **273 test liên quan Stage 1** qua. Trong lần kiểm tra handoff này, suite rộng hơn đi đến **293 test qua** rồi test `test_write_micro_moment_parses_all_three_ending_styles` lỗi tại embedding alignment vì OpenRouter/localhost bị chặn, tạo vector `nan`; run được dừng sau lỗi đó. Kết quả này **không xác nhận toàn bộ suite xanh** và chưa đủ để kết luận có regression do prompt. Một lần chạy toàn repo trước đó còn gặp FFmpeg local thiếu `drawtext` ở test art. Kiểm You.com live trong môi trường này trả `URLError`, nên test scout thực tế cần chạy lại nơi có mạng.

## 5. Bước tiếp theo cho người nhận handoff

1. **Hoàn tất phiên âm** số MP3 còn lại (1.678 tại snapshot) bằng worker resumable; không xoá hoặc ghi đè các SRT hợp lệ. Worker dùng Groq key từ môi trường ngoài repo và ghi SRT bên cạnh MP3. Vì worker đang chạy, **không khởi động thêm worker song song**. Có thể đọc trạng thái mà không gọi API hay ghi manifest:

   ```bash
   cd /Users/nhanvu/Documents/code/comic-book-pipeline
   python - <<'PY'
   import csv
   from collections import Counter
   from pathlib import Path
   path = Path('research/competitor_audio_2026-09-28/transcription_manifest.tsv')
   with path.open(newline='') as file:
       rows = list(csv.DictReader(file, delimiter='\t'))
   print(dict(Counter(row['status'] for row in rows)))
   PY
   ```

   **Chỉ khi worker hiện hữu đã dừng**, tiếp tục theo [handoff phiên âm](HANDOFF_TRANSCRIBE_ALL_COMPETITOR_MP3.md):

   ```bash
   cd /Users/nhanvu/Documents/code/comic-book-pipeline
   /Users/nhanvu/Desktop/facebook/groq-transcriber/.venv/bin/python \
     research/transcribe_worker.py --dry-run
   /Users/nhanvu/Desktop/facebook/groq-transcriber/.venv/bin/python \
     research/transcribe_worker.py --workers 2
   ```

   `--dry-run` không gọi API nhưng worker vẫn đồng bộ manifest/QC local. Sau khi chạy xong, yêu cầu 3.808 SRT hợp lệ, 0 pending; nâng QC từ 41 lên ít nhất 50 mẫu, ≥5 mỗi kênh.
2. **Chạy lại phân tích corpus sau khi đủ SRT**, đặc biệt Comic_Escape và comiczyt; giữ snapshot 2.034 của [audit hook](HOOK_OPENING_AUDIT_2026-09-30.md) để so sánh, không thay số cũ trong tài liệu. Dùng title, timestamp SRT, first spoken line, fact mới kế tiếp, narrative turn, source comic được xác minh riêng. So trong từng kênh và tách theo format (micro, list, recap), vì trộn format làm kết luận mơ hồ.
3. **Đo kênh mình** bằng *Stayed to watch*, đường retention những giây đầu, average view duration và traffic source của các Short trước/sau đổi prompt. Ghép thêm topic và thời lượng; view đối thủ không thay thế dữ liệu này. Không khẳng định “100% match reference” nếu chưa có dữ liệu hành vi người xem.
4. **Smoke test live Stage 1** trên một máy có You.com hoạt động: yêu cầu micro mở phải xếp issue năm hiện tại trước, có action + consequence + nguồn; yêu cầu đích danh issue cũ vẫn phải được tôn trọng. Kiểm comic/issue thật và xem nguồn có đủ story frame để Gemini kể. Chạy lại các test liên quan bằng lệnh ở dưới trước khi chỉnh thêm.

   ```bash
   pytest -q tests/test_micro_recency.py tests/test_research_scout_*.py \
     tests/test_youcom_micro.py tests/test_gemini_micro_moment_prompt.py
   ```

## Nguyên tắc khi tiếp tục sửa

Giữ đơn vị **một scene có setup, turn và hệ quả được nguồn hỗ trợ**. Cập nhật quy tắc chung theo bằng chứng từ nhiều reference hoặc lỗi mang tính hệ thống; không hardcode tên nhân vật, comic, page hay một câu hook vào shared code. Dữ liệu một project cụ thể có thể sửa trong `comic_context.json`/fact sheet. Chỉ dùng transcript đối thủ để tìm cấu trúc và lead; fact comic vẫn phải đến từ trang truyện, publisher preview hoặc nguồn review/synopsis đủ cụ thể.
