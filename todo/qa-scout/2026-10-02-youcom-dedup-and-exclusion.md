# R2 — You.com API, đa dạng hoá kết quả và dedup quanh bước search+LLM của scout

Ngày nghiên cứu: 2026-10-02. Cách đọc nhãn: **[doc]** = có tài liệu chính thức (kèm link), **[đo]** = tôi tự đo trên máy Mac trong phiên này, **[suy luận]** = suy luận của tôi, **[chưa xác minh]** = không kiểm chứng được. Tôi đã tải trực tiếp OpenAPI spec và các trang `.md` của docs.you.com nên mục 1 là nguồn sơ cấp, không qua tóm tắt.

---

## 0. Kết luận nhanh

1. **You.com không có cách loại trừ phía server theo URL hay theo thực thể.** Chỉ có `exclude_domains` (tối đa 500 domain). Trong Research API nó **loại trừ lẫn nhau** với `include_domains` (gửi cả hai trả 422), mà scout đang dùng whitelist 8 domain, nên không dùng được. Mọi dedup theo URL/issue phải làm phía code. [doc: OpenAPI research]
2. Research API tính tiền **theo lượt gọi và theo bậc effort, không theo token**: lite $0.012, standard $0.05, deep $0.10, exhaustive $0.45 mỗi lượt. Search là $0.005 mỗi lượt (tối đa 100 kết quả mỗi section). Vậy xin 15 ứng viên thay vì 5 không tốn thêm tiền, chỉ tốn thêm độ trễ và có thể giảm độ sâu kiểm chứng từng mục. [doc: billing]
3. Tài liệu chính thức của You.com tự ví dụ **fan-out nhiều lượt Research song song** (4 lượt, ThreadPoolExecutor) cho 4 góc độ khác nhau. Rate limit mặc định 10 req/s, không nêu giới hạn concurrency. [doc]
4. Danh sách "đừng đề xuất" dài trong prompt là **cố vấn chứ không ràng buộc**. Code của bạn đã ghi nhận You.com "IGNORES the --have list" (`stages/youcom_scout.py`). Bằng chứng học thuật: hiệu ứng "pink elephant", tụt độ tuân thủ khi mật độ chỉ dẫn tăng, "lost in the middle", "context rot". Kết luận: prompt chỉ mang **một lát cắt nhỏ liên quan**, code mới là hàng rào cứng.
5. Lát cắt liên quan lấy bằng **SQLite FTS5 + BM25** trên sổ cái. Tôi đo: top-30 trên 100.000 dòng mất khoảng 15 ms. Quét `rapidfuzz` toàn bộ 100.000 dòng mất khoảng 130 ms. Với vài nghìn mục, không cần Bloom filter, MinHash hay embedding.
6. **Cảnh báo quan trọng đo được:** `token_set_ratio("Venom #13 (2019)", "Venom #1 (2019)")` = 96,8 và `token_set_ratio("Batman #1", "Batman #1 Annual Special Edition")` = 100. Điểm mờ **không phân biệt được số issue**. Khoá issue phải là khoá có cấu trúc (series, số issue), so khớp chính xác phần số.
7. Gắn lại URL "gần đúng" an toàn nhất là **gắn theo bằng chứng**: câu trích (`quote`) phải nằm trong `sources[].snippets` của đúng một nguồn trả về, và không bao giờ gắn khi các token số khác nhau. Cách này giải đúng ca `Vol_1_2` thành `Vol_1_1`.
8. Luồng đề xuất: sổ cái SQLite (URL chuẩn hoá + khoá issue + FTS) → chọn lát cắt loại trừ nhỏ → ghép prompt **bằng code sau planner** → fan-out 3 đến 5 lượt Research `standard` theo các lát cắt trực giao (khoảng năm, loại truyện) → lọc và gắn lại URL bằng code → bù thêm tối đa 1 đến 2 vòng nếu thiếu → ghi collision vào sổ cái. Chi phí khoảng $0.15 đến $0.30 mỗi lượt scout, độ trễ khoảng 30 đến 60 giây (song song).
9. Search API ($0.005 mỗi lượt, `count` tới 100, `offset` 0 đến 9) là đường rẻ để thu nhiều URL, **lọc URL đã thấy trước khi tới LLM**, rồi trích xuất bằng LLM rẻ của bạn. Nên dùng như đường bổ sung, không thay Research cho câu hỏi cần tổng hợp đa nguồn.
10. Chưa xác minh được: `deep` khác `standard` ở chỗ nào về số nguồn, `include_domains` có khớp subdomain không, `api.you.com/v1/search` có phải alias hợp lệ của `ydc-index.io/v1/search` không (xem mục 6).

---

## 1. Năng lực của You.com API (hiện tại, tháng 10/2026)

### 1.1 Các endpoint [doc: [overview](https://you.com/docs/guides/research.md), [choose-the-right-api](https://you.com/docs/choose-the-right-api.md)]

| API | Endpoint | Giá | Ghi chú |
|---|---|---|---|
| Web Search | `POST https://ydc-index.io/v1/search` (GET vẫn chạy, không có tính năng mới) | $5 / 1.000 lượt, tối đa 100 kết quả mỗi lượt | Web và news trong cùng một response (`results.web`, `results.news`). Không có endpoint News riêng |
| Contents | `POST https://ydc-index.io/v1/contents` | $1 / 1.000 trang | Tối đa 10 URL mỗi request, `formats`, `crawl_timeout` 1 đến 60 giây, `max_age` |
| Answer | `POST https://api.you.com/v1/answer` | $5 / 1.000 lượt | `query` tối đa 400 ký tự, không có effort, p50 khoảng 2,67 giây, có `include/exclude/boost_domains`, `freshness` |
| Research | `POST https://api.you.com/v1/research` | $12 đến $1.200 / 1.000 lượt tuỳ effort | Chỉ chạy trên `api.you.com`; gửi sai host trả 403 "Missing Authentication Token" |
| Finance Research | `POST https://api.you.com/v1/finance_research` | $110 / $500 | Không hỗ trợ `source_control` và `output_schema` |

"Express / Smart agents": changelog tháng 8/2026 nhắc `/v1/agents/search`, và một blog tháng 10/2025 mô tả `POST /v1/agent/runs` với agent `express` ([blog](https://you.com/resources/build-production-ready-ai-solutions-with-you-coms-express-api-and-custom-agents)). Các trang `api-reference/agents/...` trong docs hiện trả "Page Not Found" và không có trong `llms.txt`. **[chưa xác minh]** trạng thái hiện tại; coi là legacy, không đáng dùng cho scout.

### 1.2 Research API: toàn bộ tham số [doc: [OpenAPI research](https://you.com/docs/openapi/research.json), [guide](https://you.com/docs/guides/research.md)]

- `input` (bắt buộc): tối đa **40.000 ký tự**.
- `research_effort`: `lite` (<10 giây, $12/1k), `standard` (mặc định, khoảng 10 đến 30 giây, $50/1k), `deep` (<120 giây, $100/1k), `exhaustive` (<300 giây, $450/1k), `frontier` (30 đến 12.000 giây, p50 300 giây, $1.200/1k, **bắt buộc `background:true`**, sync trả 422). Docs mô tả cơ chế: "budget-based planning", bậc cao hơn "nhiều search hơn, đọc sâu hơn, đối chiếu nhiều nguồn hơn"; một lượt có thể chạy hơn 1.000 lượt suy luận và 10 triệu token ở bậc cao. **Docs không nêu số nguồn theo từng bậc.**
- `source_control` (Beta): `include_domains[≤500]`, `exclude_domains[≤500]`, `boost_domains[≤500]`, `freshness` (`day|week|month|year|YYYY-MM-DDtoYYYY-MM-DD`), `country` (ISO alpha-2). Quy tắc: include và exclude **không đi cùng nhau** (422); boost không đi cùng include; boost đi được với exclude. `exclude_domains` còn chặn agent **duyệt** trang trên các domain đó. `boost_domains` "không đảm bảo xuất hiện". Không có trường loại trừ URL hay path.
- `output_schema` (Beta; không dùng với `lite`): gốc phải là object; mọi object phải có `properties` và `additionalProperties:false`; mọi property phải nằm trong `required` (muốn tuỳ chọn thì `["string","null"]`); không đệ quy; `anyOf` không được ở gốc. Giới hạn: độ sâu 5, tổng property 100, tổng enum 500, ngân sách chuỗi schema 25.000 ký tự. **Không hỗ trợ** (24 từ khoá): `minItems`, `maxItems`, `uniqueItems`, `minLength`, `maxLength`, `pattern`, `format`, `minimum/maximum`, `allOf`, `not`, `if/then/else`... [doc: [structured-output](https://you.com/docs/guides/research/structured-output.md)]. Khớp với việc API từ chối `minItems/maxItems` mà bạn gặp.
- `background` (bool): trả `task_id`, rồi `GET /v1/research/{task_id}` hoặc stream SSE `.../stream`. [doc: [background](https://you.com/docs/guides/research/background-requests.md)]
- Response: `output.content` (object khi có schema), `output.content_type`, `output.sources[{url,title,snippets[]}]`, và `warnings[]` ("source access issues or partial results"). **Nên log `warnings`**, hiện scout có vẻ chưa dùng. SDK Python (`youdotcom`) không có tham số ẩn nào khác [doc: [SDK model](https://raw.githubusercontent.com/youdotcom-oss/youdotcom-python-sdk/main/docs/models/researchrequest.md)].

### 1.3 Search API (POST) [doc: [OpenAPI web-search](https://you.com/docs/openapi/web-search.json), [request-controls](https://you.com/docs/guides/search/request-controls.md), [operators](https://you.com/docs/guides/search/search-operators.md)]

- `query` (không nêu maxLength trong spec), `count` 1 đến 100 mỗi section (web và news), `offset` 0 đến 9 (theo bội của `count`), `freshness`, `country`, `language`, `safesearch` (`off|moderate|strict`), `include_domains`, `exclude_domains`, `boost_domains` (≤500, include không đi cùng exclude), `knowledge:"core"` (dữ liệu có bản quyền: tài chính, thể thao, thời tiết... không liên quan truyện tranh).
- `extraction` (chỉ POST): `extraction_mode` = `highlights` (đoạn liên quan theo truy vấn, thay snippets) hoặc `full_page`; `extraction_source` = `blend|cache|fetch`; `crawl_timeout` 1 đến 60 giây (chỉ với full_page). `livecrawl` và `livecrawl_formats` là deprecated. Phí: full_page $1/1.000 trang **crawl live** (cache miễn phí); trang billing **không nêu phí riêng cho highlights** [chưa xác minh trên hoá đơn].
- Toán tử trong `query`: `site:`, `filetype:`, `+term`, `-term`, `AND`, `OR`, `NOT`, ngoặc nhóm. `-term` loại trang chứa đúng term; ví dụ chính thức chỉ có từ đơn (`-prs`, `-TensorFlow`). `-"cụm từ"` **[chưa xác minh]**.
- Mâu thuẫn tài liệu: request-controls nói `offset=1,count=10` trả kết quả 11 đến 20; spec OpenAPI nói `count=5,offset=1` trả "5 đến 10". Nên tự thử một lần.
- `livecrawl` không có phí riêng trong bảng giá chính, nhưng changelog machine-payments ghi "+$0.001 mỗi kết quả" khi dùng livecrawl. Code verify hiện dùng `GET ... count=8 livecrawl=all` nên một lượt verify có thể tới $0.005 + khoảng 16 × $0.001 ≈ $0.021 **[suy luận từ docs]**. Xem 5.5.

### 1.4 Rate limit, lỗi [doc: [rate-limits](https://you.com/docs/rate-limits.md), [errors](https://you.com/docs/using-the-api/error-code-reference.md)]

10 req/giây mặc định cho search, contents, answer, research (finance 5). Header `X-RateLimit-Limit/Remaining/Reset`; 429 kèm `Retry-After`; khuyến nghị exponential backoff trần 60 giây, tối đa 5 lần thử. Không nêu giới hạn concurrency. 422 `invalid_request` khi tổ hợp tham số sai (ví dụ include với exclude).

### 1.5 Có cách loại trừ phía server không?

**Không.** Chỉ có lọc ở mức domain. So sánh các API khác:

| Dịch vụ | Loại trừ | Ngày tháng | Ghi chú |
|---|---|---|---|
| You.com Research | `exclude_domains` ≤500, **không đi với include** | `freshness` (trang) | không có loại trừ URL/path |
| Exa | `excludeDomains` ≤**1.200**, mỗi mục có thể là domain, **domain + path prefix** hoặc wildcard `*.x.com`; `excludeText` đã deprecated | `startPublishedDate/endPublishedDate` | `numResults` ≤100, `additionalQueries` 1 đến 10 (chỉ search kiểu deep). Đây là thứ **gần nhất với "exclude_urls" phía server** [doc: [Exa search](https://exa.ai/docs/reference/search)]. Exa tính $7/1k, mỗi kết quả vượt 10 thêm $1/1k [doc: [pricing](https://exa.ai/docs/reference/pricing)] |
| Tavily | `exclude_domains` ≤150, `include_domains` ≤300 (mode `restrict`/`prefer`) | `time_range`, `start_date`, `end_date` | `max_results` 0 đến 20; 1 credit (basic) hoặc 2 (advanced) [doc: [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)] |
| Perplexity Search | `search_domain_filter` ≤20, tiền tố `-` để loại, **hỗ trợ path** (`nature.com/articles`), không trộn allow và deny | `search_after_date_filter`, `last_updated_*`, `search_recency_filter` | `query` có thể là **mảng tới 5 truy vấn trong 1 request, tính 1 đơn vị phí** [doc: [domain filters](https://docs.perplexity.ai/guides/search-domain-filters), [quickstart](https://docs.perplexity.ai/guides/search-quickstart), [pricing](https://docs.perplexity.ai/docs/getting-started/pricing)] |
| Brave | Goggles `$discard,site=...` (loại ở mức kết quả) | `freshness` `pd/pw/pm/py` + khoảng | `count` ≤20, `offset` ≤9 [doc: [Brave query](https://api-dashboard.search.brave.com/app/documentation/web-search/query)] |
| Google CSE | `siteSearch`+`siteSearchFilter=e`, `excludeTerms` | `dateRestrict`, `sort` | `start+num ≤100`; **đóng với khách mới, ngừng 01/01/2027** [doc: [overview](https://developers.google.com/custom-search/v1/overview)]. Loại |

Không dịch vụ nào có "bỏ qua các URL tôi đã xem" theo kiểu danh sách động; Exa gần nhất nhờ path-level `excludeDomains` 1.200 mục (vài nghìn mục trong sổ cái vẫn vượt trần). Theo một tổng hợp, Exa cũng không dedup phía server, người dùng tự tập hợp `seen` [nguồn thứ cấp: [theneuralbase](https://theneuralbase.com/exa-api/learn/advanced/deduplication/)]. Exa `findSimilar` dựa trên embedding, trái chủ trương bỏ embedding của bạn **[chưa xác minh chi tiết trong phiên này]**.

---

## 2. Kỹ thuật để có nhiều và đa dạng kết quả từ một search API

1. **Fan-out theo góc độ trực giao** (nhiều truy vấn hẹp thay vì một truy vấn rộng). [doc] You.com tự ví dụ 4 lượt Research song song, mỗi lượt một vùng tín hiệu ([ví dụ](https://you.com/docs/examples/biomedical-research.md)). Perplexity khuyến nghị "pass up to five related queries ... explore different angles". LangChain MultiQueryRetriever lấy hợp duy nhất của kết quả nhiều biến thể truy vấn; RAG-Fusion thêm Reciprocal Rank Fusion (RRF, hằng số k=60 theo [Cormack et al. 2009](https://dblp.org/rec/conf/sigir/CormackCB09.html)) [nguồn thứ cấp]. `enumerate` trong code bạn đã làm đúng kiểu này với 6 angle.
2. **Chia không gian đáp án theo thuộc tính trực giao** [suy luận]: khoảng năm xuất bản (2010-14, 2015-19, 2020-24, 2025+), loại truyện (one-shot, annual, event tie-in), loại nhân vật (anh hùng/phản diện), nhà xuất bản. Mỗi lượt có phần đáp án gần như rời nhau nên va chạm cấu trúc ít hơn; và luật "2010+" được thể hiện bằng chỉ dẫn **dương** thay vì cấm. `freshness` của You.com lọc theo tuổi **trang web**, không phải năm xuất bản của issue, nên **không** thay được việc này **[suy luận; chưa xác minh `freshness` áp lên trường nào của trang wiki]**.
3. **Search-then-extract**: dùng Search ($0.005, `count` tới 100, `offset` 0 đến 9, tức lý thuyết tới khoảng 1.000 kết quả mỗi truy vấn, độ sâu thực tế **[chưa xác minh]**) để thu URL rẻ, lọc URL đã thấy, rồi LLM rẻ trích xuất từ highlights. Ưu điểm lớn: **URL do code cấp**, mô hình chỉ tham chiếu `source_ref: 3`, nên không thể "tự sửa slug". Chi phí khoảng 20 lần rẻ hơn một lượt `deep`; đổi lại mất khả năng tổng hợp đa bước của Research.
4. **MMR** (Carbonell & Goldstein 1998; λ khởi đầu 0,7 theo các tổng hợp) để chọn tập kết quả đa dạng thay vì top-N thuần liên quan. Không cần embedding: dùng Jaccard từ vựng làm `Sim` [nguồn thứ cấp].
5. **Xoay domain**: với You.com, whitelist 8 domain là cố định mỗi request; xoay bằng cách gọi nhiều lượt với tập con domain khác nhau (ví dụ một lượt chỉ `reddit.com`, một lượt chỉ fandom). Điều này cũng giải bài toán Reddit lấn át (bạn đã ghi nhận ở `source_profiles.v1.json`). **[suy luận]**
6. **Phân trang**: Research không có phân trang; chỉ Search có `offset`. Vì vậy "xin thêm" ở Research nghĩa là **gọi lượt mới với lát cắt khác**, không phải trang kế.

---

## 3. Thiết kế dedup và loại trừ quanh bước search+LLM

### 3.1 Danh sách cấm trong prompt hay lọc bằng code

Bằng chứng (đều là gián tiếp; **không tìm thấy benchmark công khai nào đo trực tiếp "danh sách N mục đừng đề xuất"**):

- **Pink elephant**: nhắc tên thực thể trong prompt để cấm lại làm tăng xác suất mô hình nhắc tới nó; [Castricato et al. 2024](https://arxiv.org/abs/2402.07896) phải fine-tune bằng DPO mới xử lý được. Mô hình ngôn ngữ cũng yếu với phủ định ([Truong et al. 2023](https://arxiv.org/abs/2306.08189)).
- **Mật độ chỉ dẫn**: IFScale ([Jaroslawicz et al. 2025](https://arxiv.org/abs/2507.11538)), 500 chỉ dẫn "phải chứa từ X": chính xác chỉ ~68% ở mô hình tốt nhất; gpt-4.1 98,8% (50) → 95,4% (100) → 74,0% (250) → 48,9% (500); claude-sonnet-4 98,0% → 94,4% → 77,2% → 42,9% (số trích từ bảng trong bài, tôi đã trích PDF). Lỗi chủ yếu là **bỏ sót**, thiên về chỉ dẫn đứng sớm. Lưu ý: đo chỉ dẫn dương (phải chứa), mô hình hiện nay đã mới hơn; chỉ dùng làm tương tự cho "mỗi mục cấm là một ràng buộc".
- **Ngữ cảnh dài**: "Lost in the Middle" ([Liu et al.](https://arxiv.org/abs/2307.03172)): thông tin ở giữa bị dùng kém; [Chroma "Context Rot"](https://www.trychroma.com/research/context-rot) (18 mô hình): "ngay cả một distractor đã làm giảm hiệu năng, bốn distractor làm tệ hơn".
- **Chính hệ thống của bạn**: 80% prompt là danh sách đã làm, vẫn lặp; `Final Crisis #6` xuất hiện 5/5 lượt; "You.com IGNORES the --have list" (comment trong `youcom_scout.py`); HARD RULES ở cuối bị cắt do `digest[:9000]` (TODO #1). Đây là bằng chứng mạnh nhất vì đo trên đúng hệ thống.

**Kết luận [suy luận có cơ sở]:** hai lớp. (a) Prompt: chỉ một lát cắt **nhỏ, liên quan, đặt ở đầu hoặc cuối** (không nằm giữa), cộng luật cứng ghim cố định không bao giờ bị cắt. (b) Code: lọc cứng 100% bằng khoá có cấu trúc. Đừng coi prompt là cơ chế đảm bảo.

### 3.2 "Chỉ lấy các loại trừ liên quan" bằng từ vựng (FTS5)

- SQLite FTS5 có `bm25()` (điểm thấp hơn là tốt hơn, `ORDER BY bm25(tbl)`), truy vấn `MATCH` với `OR/AND/NOT`, cụm từ, prefix `thr*`, tokenizer `porter`, `unicode61 remove_diacritics`, và `trigram` (substring, không phân biệt hoa thường; chuỗi dưới 3 ký tự không khớp) [doc: [sqlite.org/fts5](https://sqlite.org/fts5.html)].
- **[đo]** trên 100.000 dòng tổng hợp, `bm25` top-30 với `OR` ba từ: khoảng **15 ms**; `rapidfuzz.process.extract(token_set_ratio, score_cutoff=90)` quét 100.000 dòng: khoảng **130 ms** (10.000 dòng: 12 ms). Dữ liệu thật của bạn nhỏ hơn nhiều.
- Cách dùng: planner (hoặc code) trích thực thể truy vấn (nhân vật, series, chủ đề) → `MATCH 'batman OR "no kill" OR joker'` trên bảng `items_fts(title, series, entities, reason)` → lấy K=30 đến 50 dòng. Thêm "attractors": top các khoá có `collision_count` cao cho cùng nhân vật (ví dụ Final Crisis #6). Định dạng mỗi dòng ngắn: `Final Crisis #6 (2008) — produced`. Ngân sách khoảng 1,5 đến 2,5k ký tự, **hằng số bất kể sổ cái lớn đến đâu**.
- Không có đo lường nào cho thấy K tối ưu bao nhiêu **[chưa xác minh]**; theo số IFScale (độ chính xác bắt đầu tụt quanh 100 đến 250 chỉ dẫn), giữ ≤50 là hợp lý **[suy luận]**.

### 3.3 Hash set, Bloom filter

Với vài nghìn đến vài trăm nghìn khoá: `set`/`PRIMARY KEY` SQLite là chính xác và tức thì. Bloom filter chỉ đáng khi hàng chục triệu mục, và **dương tính giả = vứt oan ứng viên mới**, trái với mục tiêu. **Khuyến nghị: không dùng** **[suy luận; công thức Bloom là kiến thức chuẩn]**.

### 3.4 Near-duplicate không embedding, ngưỡng điển hình

| Kỹ thuật | Ngưỡng/tham số tiêu biểu | Nguồn | Dùng ở đâu |
|---|---|---|---|
| ROUGE-L / lexical overlap | thêm mục mới chỉ khi ROUGE-L với mọi mục cũ **< 0,7** | Self-Instruct, [Wang et al. 2023](https://arxiv.org/abs/2212.10560) (đã đối chiếu văn bản bài) | câu hỏi/tiêu đề dài |
| MinHash + Jaccard | 5-gram từ, chữ ký 9.000, ứng viên nếu Jaccard **> 0,8**, rồi edit-similarity **> 0,8** | NearDup, [Lee et al. 2021](https://arxiv.org/abs/2107.06499) (đã đối chiếu) | tài liệu dài, trên 10^5 mục |
| SimHash | 64-bit, Hamming ≤ **3** cho 8 tỷ trang | [Manku et al. 2007](https://research.google.com/pubs/archive/33026.pdf) | tài liệu dài, quá lớn so với nhu cầu |
| rapidfuzz `token_set_ratio` | thường 85 đến 92 (quy ước cộng đồng, **không có chuẩn chính thức**); `score_cutoff` có sẵn | [docs](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html) | tiêu đề/câu hỏi ngắn |

**Bẫy đo được [đo]** (rapidfuzz, Mac):

- `token_set_ratio("Batman #1", "Batman #1 Annual Special Edition")` = **100** (docs xác nhận: tập con trả 100).
- `token_set_ratio("Venom #13 (2019)", "Venom #1 (2019)")` = **96,8**; `ratio("Batman #1","Batman #2")` = 88,9.
- Hệ quả: **không bao giờ dùng điểm mờ để quyết định hai issue là một**. Dùng khoá cấu trúc `(series_chuẩn, số_issue, [tập_phụ_đề])` so sánh chính xác phần số, đúng kiểu `issue_identity.py` và `_same_issue` bạn đã có (Jaccard ≥ 0,6 trên token series, số issue phải bằng nhau). Điểm mờ chỉ dành cho **văn bản tự do** (câu hỏi Q&A, mô tả khoảnh khắc), kèm điều kiện: tập token số hai bên phải bằng nhau.
- Ngưỡng nên **hiệu chỉnh trên cặp trùng đã biết trong sổ cái của bạn** (cặp (A,B) bạn xác nhận là cùng và khác nhau) thay vì lấy số cộng đồng. **[chưa xác minh]**: chưa hiệu chỉnh trên dữ liệu thật.

### 3.5 Chuẩn hoá URL và gắn lại URL "gần đúng"

**Quy tắc chuẩn hoá** (RFC 3986 §6.2.2 và §6.2.3: hạ chữ thường scheme/host, giải mã percent-encoding của ký tự unreserved, bỏ dot-segment, bỏ cổng mặc định, quy ước dấu `/` cuối [doc: [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986#section-6.2.2)]; `w3lib.url.canonicalize_url` sắp xếp query, bỏ fragment, chuẩn hoá percent-hex [doc: [w3lib](https://w3lib.readthedocs.io/en/latest/w3lib.html)]). `canonical_url` hiện có trong `cited_sources.py` đã bỏ `utm_*` và dấu `/` cuối nhưng **chưa**: bỏ `www.`/`m.`, bỏ scheme, giải mã percent, chuẩn hoá tiêu đề MediaWiki. Đó là nguyên nhân thêm `www.` bị loại ở TODO #7. Hàm so khớp (`match_key`, tách khỏi `canonical_url` để không đổi hành vi khác) nên làm:

1. host: chữ thường, bỏ `www.`, `m.`, `amp.`; bỏ scheme và cổng mặc định.
2. path: giải mã percent, bỏ `/` cuối, bỏ `index.html` **[suy luận]**.
3. query: bỏ `utm_*`, `fbclid`, `gclid`, `so`, `oldid`, `action`, `veaction` **[danh sách là suy luận]**, sắp xếp phần còn lại.
4. MediaWiki/Fandom: lấy tiêu đề từ `/wiki/<Title>` hoặc `?title=`; chuẩn hoá tiêu đề theo MediaWiki: `_` ≡ khoảng trắng, chữ cái đầu viết hoa [doc: [API:Query](https://www.mediawiki.org/wiki/API:Query)]. Trang issue DC Fandom có mẫu `Series_Vol_N_ISSUE`, ví dụ `dc.fandom.com/wiki/Batman_Vol_1_217` có tiêu đề "Batman (1940) #217" [nguồn: [trang mẫu](https://dc.fandom.com/wiki/Batman_Vol_1_217)], nên có thể **parse URL ra `(series, vol, issue)`** bằng regex.
5. ID-key: `batcave.biz/{id}-{slug}.html` → khoá là `id` (mẫu quan sát qua [issue hakuneko](https://github.com/manga-download/hakuneko/issues/8131)); `leagueofcomicgeeks.com/comic/{id}/{slug}` → khoá là `id` (quan sát qua [kết quả](https://leagueofcomicgeeks.com/comic/1773102/batman-1)); Reddit `/comments/{id}/...` → `id` **[suy luận từ cấu trúc Reddit, chưa xác minh trong phiên này]**.

**Gắn lại URL (near-miss re-binding) an toàn, theo tầng, dừng ở tầng đầu tiên khớp:**

- T1: `match_key(model_url) == match_key(returned_url)` → chấp nhận (sửa `www.`, `/` cuối, hoa thường).
- T2: cùng host và **khoá ID/cấu trúc bằng nhau** (cùng `(series, vol, issue)` hoặc cùng ID số) nhưng khác slug → gắn lại.
- T3 (bằng chứng): `normalize_quote(quote)` là chuỗi con của `normalize_quote(snippet)` của **đúng một** nguồn trong `sources[]` (cho phép `partial_ratio ≥ 92` cùng host nếu duy nhất) → gắn lại, ghi `rebound_from`, `rebound_reason`.
- **Chặn cứng:** nếu tập token số (số issue, vol, năm) của URL mô hình viết khác URL trả về → **không gắn bằng độ giống slug** (ca `Vol_1_2` thành `Vol_1_1`: hai URL khác số, giống nhau 99% về chữ; chỉ T3 mới được phép quyết, vì bằng chứng nằm ở quote chứ không ở slug).
- Không khớp tầng nào → loại như hiện nay (`claim_citation_url_not_returned`); không bịa.
- Lưới an toàn thứ hai đã có sẵn: bước gate hiện tải trang trích dẫn (`r.jina.ai`) và kiểm `quote_matches_source`, nên một lần gắn nhầm vẫn bị bắt ở hạ lưu **[suy luận từ code đọc được]**.
- Có thể xác minh hộp-đen tiêu đề qua MediaWiki API (`titles=...&redirects=1` trả `normalized` và `redirects`; tối đa 50 tiêu đề mỗi request) [doc: API:Query]. Dùng khi cần "URL này có tồn tại và trỏ về trang nào".

---

## 4. Over-generate rồi lọc

- **Xin N×k**: số cần xin = `ceil(mục_tiêu / tỷ_lệ_sống_sót)`. Từ TODO của bạn, tỷ lệ qua lọc rule 2010+ chỉ ~41% (21/51), nên cần 5 sống sót thì xin ≥12 đến 15. Tỷ lệ này nên **đo cuộn** (rolling) trong sổ cái theo từng mode và từng nhân vật, không đặt hằng số.
- **Khi `maxItems` không dùng được**: (1) ghi trong prompt "return up to 15; do not pad with weak items; fewer is fine"; (2) kiểm số lượng ở code (không tin schema); (3) nếu có việc **ép số lượng**, dùng slot cố định `c01..c12` kiểu `anyOf[null,{...}]`, nhưng trần tổng 100 property (mỗi ứng viên ~8 trường thì tối đa ~12 slot) và độ sâu 5 làm cách này chật **[suy luận]**; thường mảng không `maxItems` + kiểm code là đủ.
- **Chi phí/độ trễ**: giá Research **tính theo lượt**, không theo token (§1), nên xin nhiều hơn không tăng tiền; rủi ro là độ sâu kiểm chứng mỗi mục giảm vì ngân sách tính toán chia đều ("budget-based planning") **[suy luận, chưa đo]**. Đề xuất A/B: cùng một nhân vật, 5 so với 15 ứng viên, đo số mục hợp lệ cuối cùng và tỷ lệ citation hợp lệ.
- **Bù (top-up)**: nếu sau lọc còn thiếu thì gọi tiếp 1 đến 2 vòng (lát cắt mới + danh sách loại trừ **gồm cả những mục vừa va chạm**). Giới hạn cứng số vòng; vượt thì báo "lane cạn" thay vì chi tiền tiếp (khớp các ghi nhớ `scout_*_exhausted` trong `.claude/memory`).

---

## 5. Luồng đề xuất cho scout (tương thích code hiện có)

### 5.1 Sổ cái ("done-ledger") bằng SQLite

- `items(item_key PK, kind[qa|micro|recap], series_norm, issue_no, year, title, status[produced|confirmed|held|rejected], reason, question_key, first_seen, collisions INT)`.
- `urls(match_key PK, item_key NULL, status, first_seen)`.
- `items_fts` (FTS5 `porter unicode61`) trên `title, series_norm, entities, reason`.
- Nguồn sự thật: `projects/`, `qa_question_banlist.md`, `comic_candidates.csv` (như `build_scouted_digest` đang gom); sổ cái là **bản dẫn xuất tái dựng được**, tránh lệch (TODO ghi "5 project bị thiếu trong banlist").

### 5.2 Ghép prompt: code ráp, không qua planner

Thứ tự cố định: `[HARD RULES, luôn nguyên vẹn] → [TASK do planner viết, kèm angle/lát cắt] → [ALREADY HELD, lát cắt FTS ≤2k ký tự] → [nhắc lại 2 đến 3 luật cứng ngắn]`. Planner **không bao giờ chạm vào khối loại trừ** (TODO #4: planner viết lại làm mất, có lần đảo nghĩa). Không cắt cụt âm thầm: nếu vượt ngân sách thì log phần bị bỏ.

### 5.3 Gọi

- **Mặc định (Phương án B):** 3 đến 5 lượt Research `standard` ($0.05), `include_domains` như cũ, `output_schema` như cũ, chạy song song (≤10 req/s, `ThreadPoolExecutor`), mỗi lượt một lát cắt trực giao (khoảng năm × loại truyện). Hợp nhất theo khoá issue.
- **Giữ `deep` nếu A/B cho thấy hơn:** `deep` ($0.10) quan sát 17 đến 47 giây và 2 đến 12 nguồn, khá sát khoảng 10 đến 30 giây của `standard`; docs không nêu số nguồn theo bậc, nên **phải đo**: cùng 6 prompt chạy `standard/deep/exhaustive`, ghi (số nguồn, số ứng viên qua lọc, số ứng viên **mới**, $ ) → chọn theo "ứng viên mới hợp lệ trên mỗi đô la".
- **Bổ sung bằng Search (Phương án C, tuỳ chọn):** 6 đến 8 truy vấn Search ($0.03 đến $0.04) POST, `count=20`, `offset` 0 đến 2, `highlights`; loại các `match_key` đã có trong `urls`; trang còn lại đưa LLM rẻ trích xuất với `source_ref` số nguyên (URL do code cấp, **loại bỏ hoàn toàn lỗi sửa slug**).
- Header/UA: giữ `User-Agent` tuỳ chỉnh (bạn đã quan sát WAF chặn UA mặc định của urllib). Log `warnings`.

### 5.4 Lọc, gắn lại, ghi sổ

1. Hợp lệ hoá schema, rồi gắn lại URL (T1 đến T3, §3.5) trước `claim_citation_url_not_returned`.
2. Loại theo khoá issue (đã có hoặc `held/rejected/produced`), theo luật năm/định dạng (TODO #2), theo `duplicate_claim_citation`.
3. Với Q&A: yêu cầu ≥3 issue khác nhau trong một đáp án (TODO #5).
4. Mỗi ứng viên bị loại vì trùng → `collisions += 1` cho khoá đó (nuôi danh sách attractor); ghi `reason`.
5. Re-scout: các mục `confirmed` được giữ (thiết kế 2026-09-19 đã có); các mục `rejected/inconclusive` ghi vào sổ cái với `status=rejected` → **tự động vào lát cắt loại trừ ở lượt sau** (không cần đi qua planner; kiểm lại bằng code ở bước 2).

### 5.5 Chi phí ước tính mỗi lượt scout (tính từ giá đã công bố, **[suy luận]** cho giả định số lượng)

| Cấu hình | Discover | Ghi chú |
|---|---|---|
| Hiện tại | 1 × deep = $0.10 | + verify 3 đến 5 ứng viên × (~$0.005 đến ~$0.021 tuỳ livecrawl) ≈ $0.02 đến $0.10 |
| A: sổ cái + lát cắt + lọc code, 1 deep, ≤1 top-up standard | $0.10 đến $0.15 | độ trễ 20 đến 80 giây |
| B: 3 đến 5 standard song song | $0.15 đến $0.25 | độ trễ ≈ lượt chậm nhất (khoảng 10 đến 50 giây) |
| B + top-up 1 đến 2 vòng | +$0.05 đến $0.10 | giới hạn cứng |
| C: B + 6 đến 8 Search + LLM trích xuất | +$0.03 đến $0.04 + token LLM | giá LLM rẻ của bạn **[chưa xác minh]** |
| Tham chiếu | exhaustive $0.45; frontier $1.20 | không khuyến nghị cho vòng scout |

Tiết kiệm phụ: nếu bước verify của bạn đã tự tải trang trích dẫn qua `r.jina.ai`, có thể đổi Search verify sang `POST` với `extraction_source:"cache"` hoặc `highlights` để về ~$0.005 mỗi lượt **[suy luận; cần thử vì `cache` có thể thiếu `contents`]**.

### 5.6 Các chế độ lỗi

1. **Lọc mờ quá tay**: số issue khác nhau mà điểm mờ cao → dùng khoá cấu trúc, số khớp chính xác (§3.4).
2. **Lọc hụt do biến thể tên series**: "The Amazing Spider-Man" so với "Amazing Spider-Man", năm bắt đầu volume, annual → chuẩn hoá `_normal_series`; bảng alias tối thiểu.
3. **Attractor lặp** (Final Crisis #6): code loại → hiệu suất sụp → bù vòng tiêu tiền. Giảm bằng chia lát cắt dương (khoảng năm) và đặt attractor lên lát cắt loại trừ; trần số vòng; báo "lane cạn".
4. **Gắn lại URL nhầm**: chỉ tầng T3 dựa vào quote, có chặn số khác nhau, có kiểm hạ lưu; ghi `rebound_from` để audit.
5. **Sổ cái lệch**: tái dựng từ nguồn sự thật mỗi lần mở phiên; test "mọi project đã sản xuất có mặt trong sổ cái".
6. **Phụ thuộc Beta**: `source_control` và `output_schema` đang Beta; theo dõi `warnings` và 422.
7. **Không loại được URL trong domain whitelist**: `include_domains` loại trừ lẫn nhau với `exclude_domains`; mọi loại trừ URL là phía code. Muốn loại domain rác trong whitelist thì bỏ whitelist, dùng `boost_domains` + `exclude_domains` (đổi độ chính xác lấy độ phủ; cần đo).
8. **429/timeout**: backoff theo `Retry-After`; HTTP timeout > 120 giây (deep) hoặc dùng `background:true`.
9. **Reddit lấn át** khi `count` nhỏ (bạn đã ghi): tách lượt riêng cho Reddit.

---

## 6. Những gì tôi **không** xác minh được

- Tác động của bậc `deep` so với `standard` lên số nguồn, số ứng viên và độ trễ (docs chỉ mô tả định tính); cần A/B trên dữ liệu của bạn.
- Research có "tôn trọng" danh sách loại trừ trong prompt không: chỉ có quan sát của chính bạn (bỏ qua); không có benchmark công khai cho danh sách N mục.
- `include_domains` có khớp subdomain (`fandom.com` so với `dc.fandom.com`) và path không (docs `site:` nói gồm subdomain; `include_domains` im lặng). Perplexity và Exa hỗ trợ path, You.com không nêu.
- `api.you.com/v1/search` (đang dùng trong `youcom.py`) có phải alias hợp lệ của `ydc-index.io/v1/search` (docs ghi host sau) hay không; và code đang dùng GET (đóng băng tính năng mới).
- Phí của `highlights`; độ sâu phân trang thực tế (`count=100` × `offset≤9`); ngữ nghĩa `offset` (docs mâu thuẫn); giới hạn độ dài `query` của Search (spec không ghi; chính sách của bạn tự giới hạn 360 ký tự/45 từ).
- Hành vi `-"cụm từ"` trong Search; `freshness` áp lên trường ngày nào với trang wiki.
- Trạng thái hiện tại của Express/Smart agents API.
- Mẫu URL của cbr.com, comicbookroundup.com, aiptcomics.com, Reddit ngoài các ví dụ nêu ở §3.5.
- Ngưỡng `token_set_ratio` thích hợp cho dữ liệu của bạn (chưa hiệu chỉnh); số liệu thời gian là đo trên dữ liệu tổng hợp trên máy Mac.
- Chi tiết Exa `findSimilar`; giá token của LLM trích xuất (DeepSeek flash) qua OpenRouter.
- IFScale đo chỉ dẫn dương trên mô hình 2025; chưa có số liệu cho mô hình 2026 và cho chỉ dẫn phủ định.

---

## 7. Nguồn

**You.com (sơ cấp, tải trực tiếp)**
- Research guide: https://you.com/docs/guides/research.md ; API ref: https://you.com/docs/api-reference/research/v1-research ; OpenAPI: https://you.com/docs/openapi/research.json
- Source control: https://you.com/docs/guides/research/source-control.md ; Structured output: https://you.com/docs/guides/research/structured-output.md ; Background: https://you.com/docs/guides/research/background-requests.md
- Search overview: https://you.com/docs/guides/search.md ; Request controls: https://you.com/docs/guides/search/request-controls.md ; Operators: https://you.com/docs/guides/search/search-operators.md ; Page content: https://you.com/docs/guides/search/retrieve-page-content.md ; Live news: https://you.com/docs/guides/search/live-news.md ; OpenAPI: https://you.com/docs/openapi/web-search.json
- Contents: https://you.com/docs/guides/contents.md ; Answer: https://you.com/docs/guides/answer.md ; Choose the right API: https://you.com/docs/choose-the-right-api.md
- Billing: https://you.com/docs/administration/billing.md ; Rate limits: https://you.com/docs/rate-limits.md ; Errors: https://you.com/docs/using-the-api/error-code-reference.md ; Changelog: https://you.com/docs/additional-resources/changelog.md ; Index: https://you.com/docs/llms.txt
- Ví dụ fan-out: https://you.com/docs/examples/biomedical-research.md ; Deep Search: https://you.com/docs/examples/deep-search.md
- SDK: https://github.com/youdotcom-oss/youdotcom-python-sdk ; blog Express/Custom Agents (10/2025): https://you.com/resources/build-production-ready-ai-solutions-with-you-coms-express-api-and-custom-agents
- Controls announcement: https://you.com/resources/new-you-dot-com-research-api-controls

**Đối chiếu API khác**
- Exa: https://exa.ai/docs/reference/search ; https://exa.ai/docs/reference/pricing ; dedup (thứ cấp): https://theneuralbase.com/exa-api/learn/advanced/deduplication/
- Tavily: https://docs.tavily.com/documentation/api-reference/endpoint/search
- Perplexity: https://docs.perplexity.ai/api-reference/search-post ; https://docs.perplexity.ai/guides/search-domain-filters ; https://docs.perplexity.ai/guides/search-quickstart ; https://docs.perplexity.ai/docs/getting-started/pricing
- Brave: https://api-dashboard.search.brave.com/app/documentation/web-search/query ; Goggles: https://api-dashboard.search.brave.com/documentation/resources/goggles
- Google CSE: https://developers.google.com/custom-search/v1/overview ; https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list

**Học thuật và kỹ thuật**
- Lost in the Middle: https://arxiv.org/abs/2307.03172 ; Context Rot: https://www.trychroma.com/research/context-rot
- IFScale: https://arxiv.org/abs/2507.11538 ; Pink Elephants: https://arxiv.org/abs/2402.07896 ; Negation: https://arxiv.org/abs/2306.08189
- Self-Instruct: https://arxiv.org/abs/2212.10560 ; NearDup: https://arxiv.org/abs/2107.06499 ; SimHash: https://research.google.com/pubs/archive/33026.pdf
- RRF: https://dblp.org/rec/conf/sigir/CormackCB09.html ; MMR (thứ cấp): https://medium.com/@adergunov/maximal-marginal-relevance-144c23b42be5
- rapidfuzz: https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html ; SQLite FTS5: https://sqlite.org/fts5.html
- RFC 3986: https://www.rfc-editor.org/rfc/rfc3986#section-6.2.2 ; w3lib: https://w3lib.readthedocs.io/en/latest/w3lib.html
- MediaWiki: https://www.mediawiki.org/wiki/API:Query ; https://www.mediawiki.org/wiki/API:Categorymembers ; https://www.mediawiki.org/wiki/API:Search
- Mẫu URL: https://dc.fandom.com/wiki/Batman_Vol_1_217 ; https://leagueofcomicgeeks.com/comic/1773102/batman-1 ; https://github.com/manga-download/hakuneko/issues/8131

**Mã nguồn nội bộ đã đọc (chỉ đọc, không sửa):** `stages/youcom_scout.py`, `stages/research_scout/{youcom,cited_sources,issue_identity,workflow}.py`, `research_policies/source_profiles.v1.json`, `docs/superpowers/specs/2026-09-19-scout-rescout-keeping-confirmed-design.md`, `TODO_QA_SCOUT_2026-10-02.md`.
