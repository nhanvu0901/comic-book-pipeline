# Đối chiếu narration đối thủ để cải thiện writer — 2026-11-03

**Cập nhật triển khai sau nghiên cứu:** theo yêu cầu tiếp theo của Master, Terra đã cập nhật template Q&A đang dùng và generator, có review độc lập. Workspace chính đã nhận thay đổi; 134 test liên quan đạt. Chi tiết: `docs/archive/qa-writer-update-result-2026-11-03.md`. Các câu “chưa áp dụng” dưới đây ghi trạng thái tại lúc nghiên cứu; bản core minh họa không được chép nguyên khối vào production. Chưa benchmark model thực tế hoặc lượt xem.

Đợt refresh đã hoàn tất phần có thể tải: 146 metadata video mới, 77 MP3 + 77 transcript Groq, 10 MP4 mẫu. 69 video bị YouTube yêu cầu xác minh bot ngay cả sau một lần chờ và thử lại. Kho cũ đã bổ sung đủ 411/411 SRT, đạt 3.808/3.808 MP3 có transcript; không còn lỗi Groq tồn đọng. Đây là nghiên cứu và đề xuất biên tập; chưa có thử nghiệm chứng minh writer hiện tại tốt hơn sau thay đổi.

## Phạm vi và cách đọc bằng chứng

- Kho cũ: `research/competitor_audio_2026-09-28/`, 3.808 MP3 của năm kênh. Trước lượt này có 3.397 SRT; 411 bản còn lại gồm Comic_Escape 16 và comiczyt 395.
- Refresh: `research/competitor_refresh_2026-11-03/`. Xác minh ngày đăng bằng metadata từng ứng viên, loại ID cũ và video cùng/trước ngày mốc. Có 146 video hợp lệ sau 2026-09-28; riêng GiyeSears sau 2026-10-06. Không tải lại các ID đã lưu.
- Phân tích có thể tái chạy: `research/competitor_writer_analysis_2026-11-03/analyze.py`; đầu ra `corpus.jsonl`, `stats.json`, `stats.log`, thư mục `visuals/`.
- Đã kiểm tra contact sheet 10 MP4, hai video cho mỗi kênh có nội dung mới. Đây là mẫu có chủ đích để xem format, không phải mẫu ngẫu nhiên đại diện toàn kênh.
- View là snapshot, có ngày đăng nhưng không có retention hoặc viewed-vs-swiped của đối thủ. Không dùng việc video mới ít view để kết luận lời kể kém. Mẫu download bị chặn có thể làm lệch cơ cấu kênh/chủ đề trong tập transcript mới.
- Groq Whisper tạo dấu câu và đôi khi nhận sai tên hoặc thêm từ vào đoạn yên lặng. SRT cue không đồng nghĩa một câu hoặc một shot. Các thống kê từ/phút là mô tả gần đúng, không phải mục tiêu bắt writer đạt.
- “Mới” ở đây là ID mới có ngày đăng sau mốc. Chưa khẳng định các video khác ID không kể lại cùng một câu chuyện. Phụ đề YouTube lấy được cho một ID trùng Groq không được cộng thêm một video vào coverage.
- Nội dung đối thủ chỉ là bằng chứng về cách kể; không được dùng làm bằng chứng canon cho một dự án mới.


### Kho cũ sau khi hoàn tất

| Kênh | MP3 | SRT trước → sau | Median từ | Median giây | Median từ/phút |
|---|---:|---:|---:|---:|---:|
| ComicCandid | 860 | 860 → 860 | 146 | 44,1 | 200 |
| Comic_Escape | 1.095 | 1.079 → 1.095 | 173 | 54,1 | 194 |
| ComicsUnlocked | 1.319 | 1.319 → 1.319 | 136 | 43,1 | 191 |
| Flikey | 132 | 132 → 132 | 176 | 58,4 | 185 |
| comiczyt | 402 | 7 → 402 | 352 | 98,3 | 214 |
| **Tổng** | **3.808** | **3.397 → 3.808** | — | — | — |

Đã xác nhận độc lập toàn bộ SRT có block timestamp và text, không thiếu cặp MP3/SRT; QC ffprobe toàn kho có 3.808 dòng, không có flag theo ngưỡng runner. Hai lỗi 429 được retry thành công. Không sửa/ghi đè các SRT cũ hợp lệ. Bảy video đã thiếu MP3 từ lượt lưu ban đầu không nằm trong 3.808; đây là hoàn tất transcript cho audio hiện có, không tuyên bố tải được toàn bộ 3.815 title cũ.

`research/competitor_audio_2026-09-28/groq_archive_duration_qc.tsv` là bảng QC, `groq_resume_ledger.jsonl` ghi từng lần xử lý, README đã cập nhật. Tổng corpus phân tích mới là **3.885 transcript** (3.808 archive + 77 refresh), chưa bao gồm bốn file GiyeSears của lượt nghiên cứu cũ nằm riêng trên HIKSEMI.

### Số refresh đã xác minh

| Kênh | Video mới sau mốc | MP3 tải được / Groq SRT | Median từ | Median giây | Median từ/phút |
|---|---:|---:|---:|---:|---:|
| Flikey | 0 | 0 / 0 | — | — | — |
| ComicCandid | 34 | 34 / 34 | 155 | 44 | 206 |
| Comic_Escape | 36 | 32 / 32 | 205,5 | 53,5 | 238 |
| ComicsUnlocked | 24 | 7 / 7 | 168 | 48 | 210 |
| GiyeSears | 16 | 2 / 2 | 250 | 70 | 215 |
| comiczyt | 36 | 2 / 2 | 486,5 | 133,5 | 219 |

Median từng cột tính độc lập; không chia median words cho median seconds để suy ra median WPM. Thời lượng refresh lấy metadata (giây làm tròn), kho cũ dùng ffprobe khi có. Hai kênh chỉ có hai mẫu mới không đủ để suy ra xu hướng. ComicCandid và Comic_Escape có coverage tốt hơn nhưng vẫn chưa có retention; tốc độ nhanh hơn không chứng minh lời kể tốt hơn.

Chi tiết lỗi, phạm vi ngày, danh sách video và lệnh resume: `docs/archive/competitor-refresh-2026-11-03-download.md`. Không cần gọi lại Groq cho 77 SRT đã có khi tiếp tục tải 69 ID còn lại.

## Những điều quan sát trực tiếp từ 10 video mới

| Kênh / ID | Dạng kể và điểm có thể học | Điều cần kiểm soát |
|---|---|---|
| ComicCandid / `oOqAcwkS2oM` | Old Man Logan: cuộc gặp với bản thân và hậu quả cuối tạo một đường cảm xúc. | Mở bằng trạng thái cô độc tương đối chung; nhiều nhân vật phụ có thể khiến người mới mất dấu. |
| ComicCandid / `9BLJ7Rb5jYI` | Lex Luthor: nghịch lý cần kẻ thù giúp đỡ có lực hơn một phần giới thiệu lore. | Mở đầu nói việc viết bài báo khá lâu trước khi vào vấn đề đang chết. SRT nhận Lex Luthor thành Luther: không sao chép ASR như canon. |
| Comic_Escape / `c15VutkhmCg` | Spider-Man: bị đám đông từ chối, được một đứa trẻ cảm ơn, rồi được đồng đội công nhận. Kết quả cảm xúc được dựng bằng hành động cụ thể. | 83 giây có nhiều việc phụ. Nếu làm micro-moment về sự công nhận, giữ những bước làm thay đổi cảm giác, bỏ chi tiết không cần cho kết quả. |
| Comic_Escape / `oeRw2J5GU9Q` | Wolverine/Venom: vật chứa → bị lừa đâm trúng → symbiote bám → hậu quả là chuỗi hành động dễ minh họa. | Đoạn kết chuyển sang cảnh báo nguy cơ; không được coi mọi cliffhanger của đối thủ là kết thúc tốt cho mode micro của mình. |
| ComicsUnlocked / `jobGQ5mmi-o` | Peter/Oscorp: lời mời hấp dẫn → lời cảnh báo → lựa chọn → cái giá. Cấu trúc quyết định và hậu quả tạo tiến triển. | Không kể lại phần setup trong hook rồi lặp nguyên ý ở câu đầu. Không tự thêm động cơ nếu sheet chỉ ghi hành động. |
| ComicsUnlocked / `n_OJTdc09yM` | Armor: khả năng → cơ chế → mở rộng → giới hạn. Chỉ 38 giây vẫn có một đáp án đủ nghĩa. | Những so sánh tuyệt đối về độ bền phải có nguồn; ví von sức mạnh không được thay cho mô tả cơ chế. |
| GiyeSears / `5aB9-sS33sg` | Flash: câu hỏi cụ thể, ví dụ cứu người, chuyển sang cuộc đua với dịch chuyển. Hai trường hợp làm ý chính tiến lên. | Hai feat không tự chứng minh kết luận mạnh nhất trong toàn fiction. Không học câu viện dẫn các trường đại học nếu không có nguồn. SRT có từ thừa cuối clip, không coi đó là outro. |
| GiyeSears / `tGAB5OhUh4c` | Trang phục Cyclops: mỗi bộ có nguyên nhân đổi và thay đổi chức năng; list có tiến triển thay vì chỉ đọc tên. | Video ghi Part 1 và có teaser cuối; mode video độc lập của mình vẫn cần trả xong lời hứa đã đặt. |
| comiczyt / `KfZf5mJgGvI` | Anansi: mất sức mạnh → gặp nguồn gốc → yêu cầu/đề nghị → từ chối → ý nghĩa phép thử → hoàn trả. Twist được giải nghĩa bằng kết quả. | Câu dài, nhiều trạng từ; không biến độ dài này thành quy tắc chung cho Short của mình. |
| comiczyt / `bU2ylVNGaDI` | Human Torch: quan hệ được xây trước khi tình huống khoa học buộc anh trả giá. Kết bằng chia tay và phản ứng của người thân; không dừng ở tên thiết bị giải cứu. | Khoảng 172 giây; đây là recap dài trong Shorts, không phải chuẩn thời lượng micro. Giữ kết cục cảm xúc, cắt lore nếu cần thu gọn. |

Trong các frame đã kiểm tra, tám mẫu của ComicCandid, Comic_Escape, ComicsUnlocked và comiczyt chủ yếu dùng panel. Hai mẫu GiyeSears trộn panel và hoạt hình. Có caption ngắn, crop/pan và chuyển cảnh, nhưng không có bằng chứng phải thay panel bằng footage để writer kể tốt hơn.

Đã đọc sâu thêm sáu transcript có ngày đăng trước hoặc bằng 2026-10-27, chọn theo view trong phần tải được để có thêm câu chuyện đã tồn tại trên một tuần; không coi cách chọn này là phép thử nhân quả:

- Comic_Escape `fqLQVM3Y3Zs` (2026-10-14): chuỗi mục tiêu cứu mẹ → thử thách có thời hạn → gần đạt mục tiêu → bị kích động bởi đối thủ → thắng trận nhưng mất mục tiêu. Đây là ví dụ rõ nhất cho việc giữ **mục tiêu và cái giá**, thay vì kể tất cả đòn đánh với trọng lượng như nhau.
- Comic_Escape `vIaEBWK3boY` (2026-10-06): biểu diễn quyền lực → tự đặt giới hạn để chứng minh bản thân → bị tấn công ngoài dự tính. Đường kể có turn, nhưng không được chép động cơ hoặc quan hệ nếu nguồn dự án không xác nhận.
- ComicsUnlocked `LNIrRXhsHB4` (2026-10-24): nhầm người → xuất hiện đối tượng thực → lựa chọn bỏ đi → lời thách thức kéo nhân vật quay lại → hậu quả. Rõ ai phản ứng với điều gì quan trọng hơn thêm nhiều từ nối.
- ComicsUnlocked `JKR8PQ2hYxc` (2026-10-10): xuất thân → nhận symbiote → cơ chế đặc biệt → bị tách symbiote nhưng năng lực vẫn còn. Chi tiết tồn tại sau thất bại là ending cụ thể hơn lời gọi nhân vật đáng sợ.
- ComicCandid `oQSwGIggTH4` (2026-10-03): cơ chế cứu người kéo theo quyền lực và lựa chọn sai. Cần giữ đủ bước để người xem hiểu đổi thay, tránh nén thành một bảng thành tích.
- ComicCandid `UHVSh-fWDyc` (2026-10-25): một sự giúp đỡ tưởng tốt đổi thành cưỡng ép và hậu quả. Cách kể có nghịch lý; hành vi bạo lực hoặc đe dọa trong transcript đối thủ vẫn phải được xác minh riêng nếu muốn làm đề tài.

Tổng đọc sâu: 16 transcript; tổng kiểm tra hình: 10 MP4 qua contact sheet. Không có tuyên bố đã đọc thủ công từng transcript trong toàn bộ kho.

## Đối chiếu với prompt hiện tại

Đường nạp thực tế trong `stages/stage_3/gemini_prompt.py`: micro đọc `prompts/gemini_micro_moment_writer.md`, Q&A đọc `prompts/gemini_qa_writer_template.md`. `TOPIC_QA_TEMPLATE_PATH` có được khai báo nhưng không được đọc ở hàm dựng prompt hiện tại; không sửa nhầm `prompts/gemini_topic_qa_writer.md` rồi cho rằng writer UI đã thay đổi. Phải kiểm tra lại khi sửa vì các prompt đã thay đổi nhiều từ lần audit tháng 9.

| Vấn đề | Prompt hiện tại | Kết luận từ nghiên cứu |
|---|---|---|
| Hook và title hứa cùng một sự kiện | Micro đã yêu cầu cùng concrete promise và opening chain. | Giữ; cần đánh giá trên output thật, không thêm khẩu hiệu lặp. Audit cũ tìm thấy liên hệ nhỏ giữa title-hook overlap và view sau hiệu chỉnh era, không chứng minh nhân quả. |
| Câu đầu sau hook làm câu chuyện tiến lên | Đã yêu cầu comic + issue + active frame, không paraphrase hook. | Giữ yêu cầu issue của Master; gộp issue vào hành động có ích, không tự bỏ vì đối thủ ít nói issue. |
| Giải thích cơ chế và hậu quả | Micro đã yêu cầu AFTERMATH nếu có. QA đang dùng yêu cầu 2–3 beats và đã nhận các trường story_arc, motivation_detail, relationships và excerpts từ scout. | Kiểm tra packet và sheet sinh ra có thực sự chứa các bước cần hiểu kết quả; các trường giàu ngữ cảnh đã có thì phải tận dụng. Chưa xác nhận lỗi thiếu dữ kiện nếu chưa xem output dự án thật. |
| Văn nói tự nhiên | Cả hai prompt đã có nhiều đoạn gần trùng về nối câu và chống các câu rời. | Đừng thêm tiếp mười luật tương tự. Một bản core gọn + rubric đánh giá sẽ rõ hơn. |
| Giới hạn từ | Micro không có minimum nhưng có gợi ý 35–90 từ; QA nhắm 110–160 và mỗi paragraph trên 20 từ do importer. | Chọn số beat cần kể trước rồi đo thời lượng TTS. Paragraph floor là ràng buộc importer, không phải bằng chứng về lời kể hấp dẫn. |
| Ngôn ngữ đơn giản | Đã có B2, household names và ví dụ từ cấm. | Giữ khả năng hiểu khi nghe lần đầu; không đồng nhất IELTS 6.5, B2 và grade 6 thành một thước đo chính xác. Không đổi hết từ chuyên môn khi chính thuật ngữ đó là cơ chế cần giải thích. |
| Độ chính xác | Đã trace nguồn, điều kiện, phủ định, nhân quả. | Giữ. Những câu tuyệt đối/động cơ của đối thủ không phải lý do hạ gate. |

**Ba vấn đề xác nhận từ template Q&A thực sự đang dùng:**

1. Với nhiều item, phần hook bắt buộc báo đây là một list; trong khi phần source/order cho phép các item là các chặng nhân quả của một câu hỏi Why/How qua nhiều issue. Một câu trả lời giải thích không nhất thiết là list. Nên chọn hợp đồng theo loại câu hỏi: danh sách trường hợp độc lập hoặc một lập luận có tiến triển.
2. Phase 1 nói mỗi item có 40–60 từ, nhưng Phase 2 nhắm 110–160 từ cho toàn video. Với bốn item trở lên hai chỉ dẫn này không khớp. Nên có một tổng word budget rồi phân theo số item và độ phức tạp của nguồn, không giữ hai quota mâu thuẫn.
3. Story engine vẫn nói “exact construct” và tình huống khiến nhân vật “use it”, hook một item vẫn nói “odd construct”. Tuy có thêm “or thing”, khung diễn đạt vẫn thiên về một vật/cấu trúc hơn câu hỏi ai, vì sao, một quyết định hay giới hạn năng lực. Nên mô tả theo loại đáp án của câu hỏi.

Đây là các phát hiện từ văn bản prompt và đường nạp code; chưa phải kết quả benchmark sinh kịch bản. Template topic cũ có lỗi construct nặng hơn, nhưng không được dùng để kết luận về hành vi của writer UI hiện tại.

## Thứ tự cải thiện đề xuất

1. **Khớp hợp đồng Q&A trước.** Phân biệt list với Why/How, thống nhất tổng word budget, và loại khung construct riêng khỏi prompt chung. Với mỗi item/moment, cung cấp bằng chứng cho người đang làm gì, trở ngại, hành động, cơ chế (nếu cần để hiểu), và kết quả/aftermath. Ô thiếu phải được đánh dấu; không bắt mọi câu chuyện có đủ một template cố định.
2. **Đánh giá ba câu mở như một chuỗi.** Hook hứa một sự kiện; câu có issue bổ sung bối cảnh hoặc hành động mới; câu tiếp theo tạo tiến triển. Câu chỉ nhắc lại tính bất ngờ không được tính là tiến triển.
3. **Chọn đường hành động chính rồi mới cắt từ.** Bỏ mô tả/nhân vật phụ không làm thay đổi cách hiểu. Giữ bước nối nguyên nhân và kết quả, đặc biệt thứ khiến người nghe hiểu tại sao khoảnh khắc có ý nghĩa.
4. **Kết vào thay đổi cụ thể.** Hành động dẫn tới kết quả gì, quan hệ đổi ra sao, lựa chọn có giá gì. Không thay bằng một câu nói chung về số phận, và không sáng tác cảm xúc khi nguồn chỉ nói sự kiện.
5. **Nén prompt thay vì tăng prompt.** Hợp nhất các đoạn lặp, giữ riêng factual contract, narration core và audit. Chưa triển khai vì chưa có so sánh output cũ/mới trên cùng evidence packet.

## Narration core đề xuất để thử — chưa áp dụng production

```text
Use only the supplied verified evidence. Tell one connected explanation.
Choose a hook that promises the exact event or answer the script delivers.
In the first sentence after the hook, name the required comic and issue
while adding the active situation or the first new action.

Keep only the context a listener needs to understand the unusual action.
Follow the necessary steps from action to mechanism to result when sourced;
do not turn chronology into causation or infer motive.
Each sentence must add an event, explain how it works, or establish a
relationship needed for the next event. Cut repeated surprise and setup.

Use connected spoken English, mixing short emphasis with medium sentences.
Introduce supporting people by clear, consistent roles when their names
would distract. Explain a necessary unfamiliar term in everyday words.

Finish on the last supported consequence that resolves the hook.
If the source gives aftermath, tell it; if it doesn't, stop at the strongest
supported result. Do not invent closure, advertise a future twist, or add
a separate lesson. Count only spoken words and estimate duration from the
actual voice used for this project.
```

## Cách kiểm chứng writer có tốt hơn

Lấy các evidence packet đã xác minh của chính dự án, không lấy transcript đối thủ làm fact sheet canon. Giữ cùng nguồn, model, voice và word budget cho bản cũ/mới. Dùng ít nhất các loại: một cơ chế ngắn, một cảnh có lựa chọn và aftermath, một Q&A nhiều issue, một sheet mỏng. Chấm mù output theo: fidelity, hiểu được khi nghe một lần, hook được trả lời, tiến triển giữa các câu, kết cục đủ rõ và thời lượng thật. Có thể dùng fixture giả lập để thử style nhưng phải ghi rõ đó là dữ liệu giả; không dùng điểm style làm chứng minh tăng view.

Ưu tiên lỗi blocking: bịa sự kiện/nhân quả, đảo ngược điều kiện, bỏ kết cục đã có nguồn, hook không được trả lời. Các tiêu chí nhịp câu/tên riêng là tiêu chí biên tập, không phải quota cứng. Cần output writer hiện tại trên cùng packet để xác nhận thay đổi có ích; phân tích đối thủ một mình không chứng minh writer đã được sửa.
