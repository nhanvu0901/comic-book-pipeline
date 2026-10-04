# R4 — Scout "novelty-first": để chính việc tìm kiếm đi vào vùng mới (không gửi danh sách đã làm cho You.com)

Ngày: 2026-10-02. Không sửa file nào trong repo. Giá và hành vi You.com lấy từ R2 (đã xác minh ở đó); số liệu về hệ thống của ta lấy từ `TODO_QA_SCOUT_2026-10-02.md` và `DESIGN_DONE_LEDGER_2026-10-02.md`.

Nhãn: **[P]** bài báo cho thấy (tôi đã tải PDF và đọc phần liên quan). **[P-abs]** chỉ đọc abstract hoặc trang tóm tắt. **[I]** suy luận của tôi, chưa ai đo trực tiếp. **[?]** chưa xác minh. Mức đọc từng nguồn ở mục 7.

---

## 0. Kết luận nhanh

1. **Mọi họ diversification (MMR, xQuAD, IA-Select, PM-2, DPP) giữ tập đã thấy S ở phía client.** S chỉ nằm trong hàm chấm điểm khi xếp lại một *pool* do bộ truy hồi hộp đen trả về; S không bao giờ đi vào truy vấn [P]. Đó đúng là kiến trúc "ledger + post-filter" của ta. Giới hạn: xếp lại không lôi được thứ không có trong pool [I].
2. **Ngược trực giác:** Santos et al. (2012), 98 truy vấn TREC Web 2009–2010: novelty thuần kiểu MMR hơn baseline tối đa khoảng 3% và không có ý nghĩa thống kê; thứ ăn tiền là **coverage theo facet tường minh**, còn thêm novelty vào xQuAD/IA-Select không cải thiện đáng kể [P]. Với ta [I]: lọc khoá issue chính xác là hàng rào bắt buộc, nhưng đừng dùng "giống mục đã làm" để dẫn đường; hãy dẫn đường bằng facet.
3. **Nhắc danh sách "đừng trả" là yếu, kể cả khi làm đúng.** Si et al. (2024) nối tiêu đề mọi ý đã sinh vào prompt, vậy mà 4.000 ý mỗi chủ đề chỉ còn khoảng 200 ý không trùng [P]. Bộ truy hồi nơ-ron cũng kém xử lý phủ định (NevIR, ExcluIR) [P]. Khớp với *Final Crisis #6* 5/5 lượt và với R1/R2.
4. **Bộ sinh LLM hội tụ về câu trả lời "điển hình", kể cả giữa các mô hình khác nhau** (Verbalized Sampling, Artificial Hivemind) [P]. Suy ra [I]: chạy lại cùng prompt, hay đổi mô hình, không tạo đa dạng; đa dạng phải đến từ **thay đổi truy vấn**.
5. **Điều khiển dương theo thuộc tính là thứ đã được kiểm chứng:** Rainbow Teaming gán trước toạ độ ô cho mỗi lần sinh; AttrPrompt đạt hiệu năng ngang prompt đơn giản với 5% chi phí truy vấn; WideSearch chỉ đích danh "phân rã truy vấn không đầy đủ" là lỗi chính của agent [P].
6. **Chọn ô tiếp theo:** đếm lượt thăm (Go-Explore), xQuAD ở cấp facet, bandit có suy giảm vì ô cạn dần, active search [P/P-abs].
7. **Rẻ trước, đắt sau có tiền lệ:** hidden-web crawling (Ntoulas 2005, [P-abs]) và Google Deep-Web (chỉ mở rộng ô đã qua test "informative" [P]).
8. **Ước lượng cạn:** Chao1 là cận dưới số mục chưa thấy [P]; NOVA (2026) cảnh báo Good–Turing chỉ là chẩn đoán cục bộ [P].
9. **Đề xuất:** archive ô dẫn xuất từ ledger → chọn 3 đến 6 ô trực giao mỗi lượt → truy vấn Search theo facet → trích khoá issue → loại cục bộ → chỉ gọi Research cho lead chưa thấy. Khoảng **$0.15 đến $0.25 mỗi lượt**. Lợi ích đến từ **tỷ lệ lặp thấp hơn**, không phải giá rẻ hơn. Phần này là suy luận, cần A/B (mục 5.7).
10. **So với kế hoạch hiện tại:** giữ ledger và post-filter (hàng rào cứng, và là nguồn dữ liệu của archive); thêm điều khiển dương theo ô làm đòn bẩy chính cho độ mới; brief tránh chỉ còn là bảo hiểm nhỏ, đo bằng A/B.

---

## 1. Đa dạng hoá và novelty (Q1)

### 1.1 Các họ phương pháp và nơi S sống

| Phương pháp | Cách dùng S | Facet tường minh? | Ghi chú |
|---|---|---|---|
| MMR (Carbonell & Goldstein 1998) | `argmax λ·Sim1(d,Q) − (1−λ)·max_{dj∈S} Sim2(d,dj)` trên `R\S` | Không | λ=1 là xếp hạng thuần liên quan; gợi ý khởi đầu λ≈0,3 để khảo sát, sau đó λ≈0,7. Thử nghiệm 5 người dùng, chưa có ý nghĩa thống kê [P] |
| xQuAD (Santos et al. 2010) | `(1−λ)·P(d\|q) + λ·Σ_a P(a\|q)·P(d\|q,a)·Π_{dj∈S}(1−P(dj\|q,a))` | Có | Thừa số tích chiết khấu khía cạnh mà S đã phủ [P] |
| IA-Select (Agrawal et al. 2009) | `Σ_a u(a\|q,S)·v(d\|q,a)`, u giảm khi S đã phủ a | Có | Submodular, tham lam xấp xỉ (1−1/e) [P-abs] |
| PM-2 (Dang & Croft 2012) | Chia "ghế" tỷ lệ cho khía cạnh, thương Sainte-Laguë `v_a/(2·s_a+1)` | Có | Công thức từ trí nhớ [P-abs] |
| DPP (Kulesza & Taskar 2012; Chen et al. 2018) | `log det(L_S) = Σ log r_i² + log det(S_S)`, lớn khi các mục gần trực giao | Không | Greedy MAP `O(N²M)` để lấy N mục [P] |
| α-nDCG (Clarke et al. 2008) | Thước đo: gain của một nugget chiết khấu `(1−α)^{số lần đã xuất hiện}` | Có | Dùng làm số đo lặp [P] |
| TREC Novelty 2002–2004 | Trả câu "liên quan và mới so với những gì đã thấy trong chuỗi tài liệu" | Không | Khó: ngưỡng ban đầu quan trọng; bộ 2004 chỉ khoảng 8% câu trong tài liệu liên quan vừa liên quan vừa mới [P] |

Zhai et al. (SIGIR 2003) đặt tên bài toán subtopic retrieval [P-abs]; Chen & Karger (SIGIR 2006) là mốc sớm xét độ liên quan phụ thuộc giữa các tài liệu (Radlinski et al. 2008 xác nhận, gọi đó là ngoại lệ hiếm) [P].

**Điểm chung [P]:** truy hồi giai đoạn một không biết S. MMR định nghĩa S là "tập con của R đã trình cho người dùng"; Santos et al. đa dạng hoá top 100 của mô hình DPH; TREC Novelty định nghĩa "mới" so với những gì đã đọc trong chuỗi.

### 1.2 Santos et al. (2012): coverage thắng novelty [P]

- Cùng 98 truy vấn, cross-year train/test, đo ERR-IA@20 và α-nDCG@20.
- MMR và MVA ("novelty ẩn") hơn DPH nhiều nhất ~3% (α-nDCG của MVA trên WT09), không có ý nghĩa. Thêm aspect tường minh (xMMR) giúp thêm một chút; chỉ xMMR với aspect "ground-truth" hơn DPH có ý nghĩa.
- Bản **chỉ coverage** (IA-Select*, xQuAD*) "hơn hẳn" bản novelty ở hầu hết cấu hình; thêm novelty vào hai thuật toán này "không cải thiện có ý nghĩa ở ô nào của bảng". Bản chỉ coverage còn rẻ hơn (không cần vòng tham lam, O(n) xuống O(1)).
- Mô phỏng: novelty chỉ **phá thế hoà** giữa các kết quả có coverage tương đương. Số khía cạnh mỗi truy vấn khoảng một chục.

**Chuyển sang ta [I].** Có hai loại novelty: (a) *lọc khoá chính xác theo ledger* là quyết định có/không, không phải tín hiệu xếp hạng, và Santos không bác bỏ loại này; (b) *điểm giống/khác mềm so với mục đã làm* là thứ Santos cho thấy kém. Dẫn đường bằng (b) là sai hướng; dẫn đường bằng **facet tường minh** (xQuAD*) là đúng hướng.

### 1.3 Giới hạn pool và thời LLM [I từ P]

Mọi phương pháp chỉ chọn trong R. Muốn R chứa mục mới: nhiều truy vấn khác facet, `count` tới 100 và `offset` 0 đến 9 của Search API (R2). Các bài RAG đa dạng (Vendi-RAG) vẫn xếp lại pool; Carraro & Bridge báo cáo LLM xếp lại đa dạng hơn ngẫu nhiên nhưng kém phương pháp cổ điển [P-abs]. Phần thật sự mới là **LM sinh facet** (IntenT5: ngang hoặc hơn gợi ý dựa trên query log trên sáu benchmark [P-abs]).

---

## 2. Sinh truy vấn theo độ phủ và khám phá (Q2)

### 2.1 Facet và phân rã truy vấn

- **WideSearch** (ByteDance Seed, arXiv:2508.07999) [P]: 200 câu hỏi; đa số agent gần 0% thành công, tốt nhất 5%, một người làm một mình 20%. Phân tích lỗi: **"Incomplete Query Decomposition"** (không phân rã thành truy vấn con bổ trợ nhau) và recall thấp là nút thắt. Item-F1 tốt nhất trong N lần thử (N=128) gần 80, tức tìm từng dữ kiện không khó, khó ở chỗ phủ đủ (Max@N là điểm tốt nhất của một lần chạy, không phải hợp các lần). Multi-agent chia song song vượt đơn agent.
- **A-MapReduce** (arXiv:2602.01331): chia wide search thành đơn vị truy hồi nguyên tử chạy song song rồi gộp theo schema; Item-F1 +5,11% đến +17,50%, thời gian −45,8% [P-abs].
- **RAAC** (arXiv:2608.15191, 8/2026): bộ điều khiển *bên ngoài* dùng "search novelty" và "information coverage" giảm trung bình 14 lượt tìm, tăng độ chính xác tới 10% [P-abs].
- **Khai thác facet từ chính kết quả:** Dou et al. (CIKM 2011) gộp các *danh sách* lặp lại trong top kết quả để ra "chiều của truy vấn" [P-abs]. Với ta [I]: listicle, thread Reddit, category Fandom là nơi liệt kê sẵn.

### 2.2 Novelty search và quality-diversity

- **Novelty search** (Lehman & Stanley 2011) [P]: thưởng theo độ thưa (trung bình khoảng cách tới k láng giềng gần nhất trong *archive* hành vi đã thấy); vùng dày điểm thăm thưởng ít. Vượt tìm kiếm theo mục tiêu trên mê cung và đi hai chân.
- **MAP-Elites** (Mouret & Clune 2015) [P]: người dùng chọn các chiều; mỗi ô giữ lời giải tốt nhất; mỗi vòng chọn ô ngẫu nhiên, đột biến, thay nếu tốt hơn. Có bản **phân cấp**: ô lớn trước, chia nhỏ sau.
- **QDAIF** (Bradley et al. 2023) [P]: LM vừa sinh vừa chấm chất lượng và đa dạng; QD score là tổng chất lượng tốt nhất mỗi ô; phủ nhiều vùng hơn đối chứng không-QD.
- **Rainbow Teaming** (Samvelyan et al.) [P]: **chọn trước toạ độ ô của ứng viên rồi mới sinh**, để (i) khỏi cần bộ phân loại gán ô, (ii) giảm thiên lệch của bộ sinh vốn hay bỏ sót cả nhóm, (iii) không tốn lượt vào vùng đã có lời giải, bằng cách thiên phân phối chọn về ô fitness thấp. Thử trên QA (chủ đề × từ nghi vấn × độ dài): coverage 0,97 so với 0,90 và self-BLEU 0,50 so với 0,60 của baseline "sinh từ đầu không dùng archive".
- **Go-Explore** (Ecoffet et al., Nature 2021) [P]: archive ô, "chọn ô hứa hẹn → quay lại → khám phá → ô mới thì thêm". Hiệu quả phụ thuộc vào **biểu diễn ô chỉ chứa đặc trưng liên quan tới khám phá**.

**Căng thẳng với ràng buộc của ta [I].** Rainbow và QDAIF dùng lời giải cũ làm "bậc đá"; Rainbow cho thấy bỏ bậc đá thì coverage giảm (0,97 xuống 0,90). Ta không gửi nội dung mục đã làm, chỉ gửi **toạ độ** ô, nên có thể mất một phần lợi ích này.

### 2.3 Vì sao LLM và engine cứ lặp mục phổ biến

- Si, Yang & Hashimoto (2024) [P]: 4.000 hạt giống ý tưởng mỗi chủ đề, prompt đã nối tiêu đề mọi ý trước đó, vẫn chỉ ~5% (khoảng 200) không trùng; tỷ lệ không trùng mỗi lô giảm dần rồi chạm trần.
- Verbalized Sampling (Zhang et al. 2025) [P]: thiên lệch điển hình của người gán nhãn (α̂ ≈ 0,57 đến 0,65) gây mode collapse; yêu cầu nêu phân phối kèm xác suất tăng đa dạng 1,6 đến 2,1 lần và **chỉnh được bằng ngưỡng xác suất** (ngưỡng thấp thì đa dạng hơn).
- Artificial Hivemind (Jiang et al., NeurIPS 2025 D&B) [P]: tự lặp trong một mô hình và **đồng nhất giữa 70+ mô hình**, nên ensemble nhiều mô hình có thể không tạo đa dạng thật. Kandpal et al. (ICML 2023): LLM kém tri thức đuôi dài [P-abs].
- Giả thuyết cho *Final Crisis #6* [I]: thiên lệch điển hình của bộ sinh cộng với độ nổi tiếng trong xếp hạng của engine, hai lực cùng kéo về mục nổi tiếng. Không bài nào đo riêng You.com.

### 2.4 Điều khiển dương mạnh hơn danh sách phủ định

- **AttrPrompt** (Yu et al., NeurIPS 2023 D&B) [P]: prompt có thuộc tính hơn prompt chỉ theo lớp, ngang hiệu năng với **5% chi phí** truy vấn ChatGPT; đa dạng thuộc tính là yếu tố then chốt.
- **Evol-Instruct** (Xu et al., ICLR 2024) [P]: "in-breadth evolving" yêu cầu prompt mới "cùng miền nhưng *hiếm hơn*", một chỉ dẫn dương không cần danh sách phủ định.
- **Phủ định trong truy hồi:** NevIR (Weller et al., EACL 2024): đa số mô hình IR cho kết quả bằng hoặc kém ngẫu nhiên khi hai tài liệu chỉ khác ở phủ định [P]. ExcluIR (Zhang et al., AAAI 2025): bộ truy hồi hiện có "chật vật" với truy vấn loại trừ [P]. Negative relevance feedback cũng là trường hợp khó của IR cổ điển (Wang, Fang & Zhai, SIGIR 2008) [P-abs].

### 2.5 Khám phá và khai thác khi tìm tuần tự

- **Ranked bandits** (Radlinski et al., ICML 2008) [P]: mỗi vị trí một bandit; thưởng chỉ khi mục ở vị trí đó được chọn *và chưa được vị trí trước thoả mãn*, tức học **giá trị biên** thay vì giá trị tuyệt đối; regret `(1−1/e)·OPT − O(k·√(T·n·log n))`. Là bản bandit của xQuAD/IA-Select.
- **Active search** (Garnett et al., ICML 2012) [P-abs]: khám phá nhiều thành viên của một lớp nhất có thể bằng ít truy vấn nhất; đúng khuôn "số mục mới hợp lệ trên mỗi lượt".
- **Rotting bandits** (Levine et al., NeurIPS 2017) [P-abs]: phần thưởng kỳ vọng của cánh *giảm* theo số lần kéo, đúng với ô cạn dần. **Count-based exploration** (Tang et al., NeurIPS 2017) [P-abs] là "ít phủ nhất". UCB1 và Thompson sampling là nền chuẩn [M].

---

## 3. Điều khiển dương: ô và giao ô chưa phủ (Q3)

### 3.1 Chiều của ô (tổng quát, không gắn truyện cụ thể)

Theo Go-Explore chỉ giữ chiều liên quan tới độ mới; theo Madhavan et al. giới hạn tối đa 3 chiều mỗi mẫu (họ thấy mẫu trên 3 chiều "hầu như không bao giờ informative") [P].

| Chiều | Ví dụ giá trị | Cứng/mềm |
|---|---|---|
| Năm phát hành issue | 2010 đến 2026, hoặc khối 3 đến 4 năm | Mềm (kiểm lại bằng metadata Fandom) |
| Nhà xuất bản/imprint | DC, Marvel, Image, IDW… | Mềm |
| Nhân vật/nhóm | lấy từ ledger và Fandom | Mềm |
| Hình thức | one-shot, mini-series, run dài, annual, tie-in | Mềm |
| Angle (micro) hoặc kiểu câu hỏi (Q&A) | các angle trong `general_angles.v1.json` | Mềm |
| Nguồn | Reddit, listicle, wiki, review | **Cứng**: `include_domains` |

Chiều cứng là chiều API ép được; chiều mềm chỉ ép bằng prompt rồi kiểm lại bằng code, nên phải đo tỷ lệ tuân thủ (mục 5.5).

### 3.2 Archive (dẫn xuất từ ledger, không phải nguồn sự thật thứ hai)

Mỗi ô lưu: `n_done` (đã sản xuất), `n_seen` (đã gặp nhưng bị loại), `calls`, `new_valid`, `best_quality` (kiểu QD), `chao_lb`. Tách "done" và "seen-rejected": mục bị loại vẫn là mục đã gặp (không còn mới) nhưng không làm ô trông bão hoà.

### 3.3 Chọn ô

| Bộ chọn | Công thức tối giản | Bằng chứng | Nhận xét |
|---|---|---|---|
| Ngẫu nhiên đều | uniform | MAP-Elites [P] | Phí vào ô trống |
| Ít phủ nhất | `1/(1+n_c)` | Go-Explore, count-based [P/P-abs] | Không biết ô nào còn mục thật |
| Thiên về ô yếu | chọn có trọng số theo "thiếu" | Rainbow Teaming [P] | Cần định nghĩa fitness |
| xQuAD cấp facet | `P(c)·(1 − min(1, n_c/τ_c))` | dạng coverage của Santos [P]; áp dụng [I] | Cần sức chứa τ_c |
| Thompson có suy giảm | Gamma–Poisson trên "mục mới hợp lệ mỗi lượt", chiết khấu theo tuổi | rotting/ranked bandits, active search [P/P-abs]; công thức [I] | Học được ô đáng tiền; cần lượt khởi động |
| PM-2 | thương `v_c/(2·s_c+1)` | Dang & Croft [P-abs] | Chia lượt theo tầm quan trọng |

Đề xuất [I]: pha 1 dùng "ít phủ nhất có sàn khám phá" (không cần dữ liệu), pha 2 chuyển Thompson có suy giảm khi đã đủ lượt đo. Chọn **một lô** ô mỗi lượt, chiết khấu các ô cùng giá trị facet sau mỗi lần chọn (tinh thần `Π(1−…)` của xQuAD) để lô trải đều các trục.

### 3.4 Bài thử "ô có đáng tiền" trước khi trả tiền đắt

Madhavan et al. (VLDB 2008) [P]: tích Descartes nổ tung (form cars.com 5 đầu vào cho hơn 240 triệu URL trong khi chỉ có 650.000 xe) nên họ định nghĩa **informativeness test**: mẫu là informative nếu tỷ lệ chữ ký trang khác nhau `|S|/|G| ≥ τ` (τ=0,25). ISIT bắt đầu từ mẫu 1 chiều, chỉ mở rộng mẫu informative, trần 10.000 URL mỗi mẫu; số mẫu phải thử tăng *tuyến tính* thay vì mũ; trung bình chỉ vài trăm URL mỗi form mà vẫn phủ tốt. Họ cũng thấy số site phủ được quan trọng hơn độ sâu mỗi site (top 10.000 form chỉ chiếm 50% kết quả Deep-Web trên Google, 100.000 form chiếm 85%). Chuyển sang ta [I]: một lượt Search ($0.005) làm bài thử 1 chiều; ô cho ra ít khoá lead mới (hoặc toàn khoá đã có) thì chưa mở rộng sang Research.

### 3.5 Phủ tối đa và điều kiện dừng

- Chọn truy vấn để phủ nhiều bản ghi với chi phí thấp là set cover/minimum weighted dominating set, NP-đầy đủ; thực tế dùng tham lam (Wu et al., ICDE 2006) [P-abs]; hàm submodular có bảo đảm (1−1/e) [P qua Radlinski].
- **Chao1 trên "incidence"** (Hajič & Moss 2025, kho thánh ca) [P]: coi mỗi lượt scout là một mẫu; `f1` là số khoá chỉ gặp ở đúng một lượt, `f2` đúng hai lượt; `S = S_obs + f1²/(2·f2)`. Là **cận dưới** nên độ phủ ước lượng là **cận trên** (0,5 nghĩa là đã thấy nhiều nhất một nửa); họ chọn Chao vì thận trọng, tránh đánh giá thừa giá trị của thu thập tiếp.
- **NOVA** (Avestimehr, Duffy & Médard, arXiv:2605.15219, bản thảo chưa phản biện) [P]: Good–Turing là chẩn đoán đa dạng *cục bộ của lô*, không ước lượng được phần giá trị chưa khám phá theo lịch sử; dưới đuôi Zipf số mũ α>1, chi phí tích luỹ để có D mục thật khác nhau là `Θ(c_gen·D^α)`, tức mục thứ D đắt dần, và khi lấy mẫu tự trị đình trệ vì lặp thì cần **hướng dẫn từ bên ngoài** làm đổi phân phối lấy mẫu. Đây là lý do dẫn đường theo ô đáng giá hơn chỉ lọc sau.
- Vì mẫu của ta thiên về mục nổi tiếng nên Chao1 vẫn chỉ là cận dưới [I]. Dùng quy tắc kép: `chao_lb` thấp **và** N lượt liền không có mục mới hợp lệ mới đánh dấu "lane cạn".

---

## 4. Search-then-filter với truy hồi rẻ (Q4)

**Bằng chứng:**
- Ntoulas, Zerfos & Cho (JCDL 2005) [P-abs]: chọn truy vấn kế tiếp theo hướng tài liệu *mới* (giữ tập đã tải ở client) tải hơn 90% một site 14 triệu tài liệu sau dưới 100 truy vấn. Wu et al. (ICDE 2006) [P-abs] mô hình hoá thành set cover.
- Madhavan et al. (2008) [P]: chỉ "bề mặt hoá" tổ hợp informative; xem 3.4.
- Cascade (Wang, Lin & Metzler, SIGIR 2011; Nogueira et al., arXiv:1910.14424) [P-abs]: chi phí tầng đắt được kiểm soát bằng số ứng viên *được nhận vào* mỗi tầng.
- Câu hỏi nhiều đáp án rải nhiều đoạn (QAMPARI, GEM 2023; QUEST, ACL 2023): khó cả truy hồi lẫn sinh; mô hình tốt nhất QAMPARI chỉ F1 32,8 [P]. Cùng bản chất với Q&A "đáp án trải nhiều issue" của ta.

**Cho ta [I]:**
1. Giữ tập đã thấy ở client là cách đã được chứng minh.
2. Giai đoạn rẻ nên **khuếch tán** (nhiều truy vấn theo facet) để mở rộng R; giai đoạn đắt chỉ nhận lead chưa thấy.
3. **Khoảng trống:** Search API trả bài viết, không phải danh sách issue. Cần bước **trích khoá issue** (regex URL Fandom/batcave; LLM rẻ cho snippet hoặc trang listicle lấy qua Contents `$0.001/trang`). Chất lượng bước này quyết định "mới giả" hay "mới thật", nên khoá cấu trúc (R2 §3.4) là bắt buộc.
4. Một biến thể về "mẫu số": nếu có danh mục có cấu trúc (category MediaWiki của Fandom) thì liệt kê vũ trụ ứng viên không cần search rồi lấy hiệu tập với ledger, và có luôn `τ_c` cho 3.3. Chưa kiểm khả thi [?].

---

## 5. Tổng hợp: scout "novelty-first"

### 5.1 Luồng mỗi lượt

```
ledger (JSONL, nguồn sự thật) --dẫn xuất--> ARCHIVE ô: n_done, n_seen, calls, new_valid, chao_lb
(1) cell selector: K=3..6 ô trực giao
(2) truy vấn facet do code dựng (KHÔNG chứa tên mục đã làm): Search x K..2K ($0.005/lượt, count<=100, offset 0..2)
(3) thu URL/snippet; Contents cho 10..20 trang liệt kê ($0.001/trang); trích khoá issue (regex URL + LLM rẻ)
(4) loại cục bộ theo khoá cấu trúc (đã làm / đã gặp / bị loại); ghi va chạm; xếp hạng lead mới (đa nguồn, đúng facet)
(5) Research standard ($0.05) CHỈ cho 2..3 cụm lead chưa thấy; prompt = "kiểm chứng và viết khoảnh khắc/đáp án cho các lead sau" (danh sách DƯƠNG)
(6) post-filter cứng (năm, định dạng, >=3 issue cho Q&A, khoá) -> ghi ledger -> cập nhật bandit (thưởng = mục mới hợp lệ trên đô la, theo ô)
dừng/đổi ô khi chao_lb thấp VÀ N lượt liền không có mục mới
```

Không bước nào gửi tên mục đã làm cho You.com; prompt Research chỉ chứa **lead cần kiểm chứng** do code chọn.

### 5.2 Truy vấn theo facet (mẫu, không gắn truyện cụ thể)

- Search: `{ý định hoặc angle} {nhân vật} {khối năm} {nhà xuất bản} issue`, xoay `site:` (reddit, fandom, listicle) làm chiều nguồn.
- Nếu dùng Research để discover: "tìm issue xuất bản {khối năm}, hình thức {hình thức}, cho {ý định}; mỗi mục có số issue và năm; ưu tiên mục ít được nhắc". Chỉ dẫn "hiếm hơn" học từ Evol-Instruct và ngưỡng xác suất thấp của Verbalized Sampling, nhưng **phải đo** vì chưa ai thử trên You.com [I]. `freshness` lọc theo tuổi *trang* (R2), không ép được chiều năm issue.

### 5.3 Chi phí mỗi lượt (giá R2; số lượng là giả định)

| Cấu hình | Search | Contents | LLM trích xuất | Research | Tổng |
|---|---|---|---|---|---|
| Nhẹ | 6 × $0.005 = $0.03 | 10 trang = $0.01 | ≈$0.01–0.02 [giả định] | 2 standard = $0.10 | **≈$0.15** |
| Chuẩn | 8 = $0.04 | 20 trang = $0.02 | ≈$0.02 [giả định] | 3 standard = $0.15 | **≈$0.23** |
| Nặng | 12 = $0.06 | 30 trang = $0.03 | ≈$0.03 [giả định] | 3 deep = $0.30 | **≈$0.42** |
| Bù vòng (tối đa 2) | +4 Search mỗi vòng | | | | +$0.02 đến $0.04 |

Hiện tại: 1 deep ($0.10) cộng verify 3–5 mục ($0.02–0.10) ≈ $0.12–0.20; phương án B của R2 (3–5 standard song song) ≈ $0.15–0.25. Giá LLM trích xuất qua OpenRouter chưa kiểm [?].

**Mô hình giá trị [I]:** `NVI = n × (1 − ρ) × v` (n ứng viên trả về, ρ tỷ lệ đã có trong ledger, v tỷ lệ qua luật cứng). Từ TODO: n ≈ 5,1/lượt, v ≈ 0,41 (21/51 qua luật 2010+); chưa có ρ vì ledger chưa tồn tại. Minh hoạ: nếu ρ ≈ 0,5 thì NVI ≈ 1,05/lượt, tức khoảng 5–9 NVI/$. Thiết kế mới thắng khi `(1−ρ_mới)·v_mới / chi phí_mới` lớn hơn con số đó; khi ρ cũ cao, chi phí tăng 50% vẫn có lợi nếu ρ giảm một nửa. Đây là ví dụ có giả định, **không phải dự báo**.

### 5.4 Chế độ lỗi

1. **Ô rỗng/giả** (tổ hợp facet không tồn tại): bài thử 1 chiều (3.4), `n_seen` thấp.
2. **Engine bỏ qua facet, vẫn trả mục nổi tiếng** (bằng chứng gián tiếp, 2.3): ép chiều cứng (domain), kiểm tuân thủ bằng metadata; tuân thủ thấp thì bỏ facet đó.
3. **Khoá sai nên "mới giả":** khoá cấu trúc, số khớp chính xác (R2: `token_set_ratio("Venom #13", "Venom #1") = 96,8`).
4. **Ô quá mịn:** phân cấp thô-trước-mịn-sau như MAP-Elites.
5. **Bandit hội tụ vào vài ô dễ:** sàn khám phá, chiết khấu theo tuổi, tái khởi động định kỳ.
6. **Chất lượng tụt ở vùng hiếm:** theo dõi `best_quality` mỗi ô và giữ ngưỡng chấp nhận chung (QD tính cả chất lượng, không chỉ phủ).
7. **Song song dư thừa:** nhiều lượt cùng prompt gần như trùng (Hivemind, Si); mỗi lượt song song **phải** khác ô, không phải khác seed.
8. **Chao1 lệch vì mẫu không đồng nhất:** chỉ là cận dưới (3.5).
9. **Search API chưa thử trên truyện tranh:** snippet có thể không chứa issue cụ thể; đo "khoá lead mới mỗi $0.005" trước khi đầu tư [?].
10. **Overfit:** mọi chiều ở 3.1 là tổng quát; không hard-code tên truyện hay số issue (nguyên tắc `CLAUDE.md`).

### 5.5 Số đo

| Số đo | Định nghĩa |
|---|---|
| **NVI/$** (chính) | mục mới hợp lệ vượt gate / tổng chi (Search + Contents + LLM + Research + verify) |
| Tỷ lệ lặp ρ hai tầng | ρ_search: khoá lead đã có trong ledger / tổng lead; ρ_research: ứng viên đã có / tổng ứng viên |
| Độ tập trung attractor | thị phần top-5 khoá trong mọi lần nhắc (hoặc HHI); *Final Crisis #6* là ví dụ |
| Tuân thủ facet | ứng viên đúng ô được yêu cầu / ứng viên trả về |
| Phủ ô | số ô có ≥1 mục đã làm / số ô đang xét, kèm `chao_lb` (cận trên độ phủ) |
| Hiệu suất Research | mục hợp lệ / lượt Research |
| α-nDCG kiểu lặp | gain chiết khấu `(1−α)^{lần đã thấy}`, khoá đã làm cho 0 (Clarke et al.) |

### 5.6 So với kế hoạch hiện tại

| Tiêu chí | Hiện tại (ledger + brief tránh nhỏ + post-filter) | Novelty-first | Khuyến nghị lai |
|---|---|---|---|
| Cơ chế tạo mới | Phủ định (brief) + lọc sau | Dương (ô chưa phủ, truy vấn facet) | Dương chính, lọc sau là hàng rào |
| Gửi tên mục đã làm | Có (~2k ký tự) | Không | Không, hoặc ≤10 khoá attractor (A/B) |
| Chi phí/lượt | $0.10–0.15 (A), $0.15–0.25 (B) | ≈$0.15–0.25 | ≈$0.15–0.25 |
| Bằng chứng cho đòn bẩy chính | Yếu (Si, NevIR/ExcluIR, tự quan sát) | Trung bình (Rainbow, AttrPrompt, WideSearch, Santos), chưa thử trên You.com | Cả hai |
| Độ phức tạp | Thấp | Cao (archive, selector, trích xuất) | Trung bình, làm theo pha |
| Rủi ro chính | Vẫn lặp attractor, tốn tiền ở vòng bù | Facet bị bỏ qua, ô rỗng | Đo tuân thủ facet trước |

**Lộ trình [I]:** (1) dựng archive dẫn xuất từ ledger kèm bảng đo ρ và attractor, gần như miễn phí; (2) biến "angle" hiện có thành ô hai chiều (khối năm × hình thức) và chạy 3–5 lượt standard song song mỗi lượt một ô (khớp phương án B của R2), chỉ đổi *cách chọn ô*, chưa cần Search; (3) nếu NVI/$ và tuân thủ facet đủ tốt thì thêm tầng Search + trích xuất, sau khi đo "khoá lead mới mỗi $0.005".

### 5.7 Thiết kế A/B (≈$5–8)

Bốn nhánh, mỗi nhánh ~10 lượt trên cùng tập seed và cùng ledger đóng băng: (A) hiện tại; (B) ledger + brief + post-filter; (C) chọn ô + Research song song, không brief; (D) Search fan-out + dedup + Research cho lead mới. Ghi chi phí, ρ hai tầng, attractor, tuân thủ facet, NVI, thời gian.

### 5.8 Bằng chứng so với suy luận

| Khẳng định | Mức |
|---|---|
| Truy hồi giai đoạn một không thấy S | [P] |
| Novelty mềm kém, coverage tường minh tốt | [P], phạm vi: web search nhập nhằng, chưa phải truy hồi issue truyện |
| Nối danh sách trước vào prompt vẫn lặp ~95% | [P], phạm vi: sinh ý tưởng, không phải truy hồi web |
| Bộ truy hồi nơ-ron yếu với phủ định | [P], phạm vi: mô hình nơ-ron, không phải You.com |
| Gán ô trước + thiên ô yếu tăng coverage | [P], phạm vi: sinh prompt đối kháng và câu hỏi trivia |
| Truy vấn theo facet làm You.com ra mục mới | **[I]**, chưa ai đo |
| Giảm ρ đủ bù chi phí cao hơn | **[I]**, cần A/B |
| Chao1 là cận dưới hợp lý cho số mục chưa thấy | [P] ở miền khác; áp dụng cho scout là [I] |
| Số lead mới mỗi lượt Search trên truyện tranh | **[?]** |

---

## 6. Những gì chưa xác minh

- Không bài nào thử Research API hộp đen kiểu You.com với truy vấn facet hay danh sách loại trừ; mọi chuyển giao sang You.com là suy luận.
- Chưa đọc toàn văn: Ntoulas 2005 (không tải được), Wu et al. 2006, xQuAD và IA-Select gốc, PM-2, Chen & Karger, Zhai et al. Công thức xQuAD/IA-Select lấy từ Santos et al. 2012 (đã đọc); Sainte-Laguë của PM-2 từ trí nhớ.
- Số liệu A-MapReduce, RAAC, IntenT5, Vendi-RAG lấy từ trang tóm tắt. NOVA là bản thảo chưa phản biện; tôi đọc abstract và mục đầu.
- Giá LLM trích xuất, độ sâu phân trang thực của Search (`count=100` × `offset≤9`), tuân thủ facet của You.com, và khả thi của "mẫu số" từ category Fandom chưa đo.

---

## 7. Tài liệu tham khảo (T: đọc toàn văn qua PDF; A: abstract hoặc tóm tắt; M: trí nhớ)

**Diversification, novelty**
- Goldstein & Carbonell, *Summarization: (1) Using MMR for Diversity-Based Reranking and (2) Evaluating Summaries*, TIPSTER 1998. https://aclanthology.org/X98-1025/ (T). Bản ngắn: Carbonell & Goldstein, SIGIR 1998, tr. 335–336 (A).
- Santos, Macdonald, Ounis, *On the role of novelty for search result diversification*, Information Retrieval Journal 2012, doi:10.1007/s10791-011-9180-x. http://terrierteam.dcs.gla.ac.uk/publications/santos2012irj.pdf (T).
- Santos, Macdonald, Ounis, *Exploiting query reformulations for web search result diversification (xQuAD)*, WWW 2010, tr. 881–890. https://dl.acm.org/doi/abs/10.1145/1772690.1772780 (A).
- Agrawal, Gollapudi, Halverson, Ieong, *Diversifying search results (IA-Select)*, WSDM 2009 (A). Dang & Croft, *Diversity by proportionality (PM-2)*, SIGIR 2012 (A/M). Zhai, Cohen, Lafferty, *Beyond independent relevance*, SIGIR 2003, doi:10.1145/860435.860440 (A). Chen & Karger, *Less is more*, SIGIR 2006, doi:10.1145/1148170.1148245 (A).
- Clarke et al., *Novelty and diversity in information retrieval evaluation (α-nDCG)*, SIGIR 2008. https://plg.uwaterloo.ca/~gvcormac/novelty.pdf (T).
- Kulesza & Taskar, *Determinantal point processes for machine learning*, FnTML 2012, arXiv:1207.6083 (A). Chen, Zhang, Zhou, *Fast greedy MAP inference for DPP*, NeurIPS 2018, arXiv:1709.05135 (T).
- Soboroff & Harman, *Novelty detection: the TREC experience*, HLT/EMNLP 2005. https://www-nlpir.nist.gov/works/papers/soboroff/hlt-novelty.pdf (T).
- MacAvaney et al., *IntenT5*, arXiv:2108.04026 (A). Rezaei & Dieng, *Vendi-RAG*, arXiv:2502.11228 (A). Carraro & Bridge, arXiv:2401.11506 (A).

**Sinh theo độ phủ, quality-diversity, đa dạng LLM**
- Lehman & Stanley, *Abandoning objectives: evolution through the search for novelty alone*, Evol. Comput. 19(2):189–223, 2011, doi:10.1162/EVCO_a_00025 (T). Mouret & Clune, *Illuminating search spaces by mapping elites*, arXiv:1504.04909 (T).
- Bradley et al., *Quality-Diversity through AI Feedback*, arXiv:2310.13032 (T). Samvelyan et al., *Rainbow Teaming*, arXiv:2402.16822 (T). Ecoffet et al., *First return, then explore*, Nature 590, 2021, arXiv:2004.12919 (T).
- Si, Yang, Hashimoto, *Can LLMs generate novel research ideas?*, arXiv:2409.04109 (T). Zhang et al., *Verbalized Sampling*, arXiv:2510.01171 (T). Jiang et al., *Artificial Hivemind*, NeurIPS 2025 D&B, arXiv:2510.22954 (T). Kandpal et al., ICML 2023 (A).
- Yu et al., *AttrPrompt*, NeurIPS 2023 D&B, arXiv:2306.15895 (T). Xu et al., *WizardLM (Evol-Instruct)*, ICLR 2024, arXiv:2304.12244 (T).
- Weller, Lawrie, Van Durme, *NevIR*, EACL 2024, arXiv:2305.07614 (T). Zhang et al., *ExcluIR*, AAAI 2025, arXiv:2404.17288 (T). Wang, Fang, Zhai, *Negative relevance feedback*, SIGIR 2008, tr. 219–226 (A).
- Wong et al., *WideSearch*, arXiv:2508.07999 (T). Chen et al., *A-MapReduce*, arXiv:2602.01331 (A). Soudani et al., *When deep research agents stagnate (RAAC)*, arXiv:2608.15191 (A). Dou et al., *Finding dimensions for queries*, CIKM 2011, tr. 1311–1320 (A).- Amouyal et al., *QAMPARI*, GEM 2023. https://aclanthology.org/2023.gem-1.9/ (T). Malaviya et al., *QUEST*, ACL 2023, arXiv:2305.11694 (T).

**Bandit, khám phá, ước lượng cạn**
- Radlinski, Kleinberg, Joachims, *Learning diverse rankings with multi-armed bandits*, ICML 2008. https://www.cs.cornell.edu/people/tj/publications/radlinski_etal_08a.pdf (T).
- Garnett et al., *Bayesian optimal active search and surveying*, ICML 2012, arXiv:1206.6406 (A). Levine, Crammer, Mannor, *Rotting bandits*, NeurIPS 2017, arXiv:1702.07274 (A). Tang et al., *#Exploration*, NeurIPS 2017 (A). Slivkins, arXiv:1904.07272 (M). Auer, Cesa-Bianchi, Fischer, Machine Learning 2002 (M).
- Hajič & Moss, *Knowing when to stop: insights from ecology for building catalogues, collections, and corpora*, arXiv:2507.14614 (T). Avestimehr, Duffy, Médard, *NOVA*, arXiv:2605.15219 (T, abstract và mục đầu).

**Truy hồi rẻ rồi lọc**
- Madhavan et al., *Google's Deep-Web Crawl*, PVLDB 1(2):1241–1252, 2008 (T). Ntoulas, Zerfos, Cho, *Downloading textual hidden web content through keyword queries*, JCDL 2005, tr. 100–109. https://dl.acm.org/doi/10.1145/1065385.1065407 (A). Wu, Wen, Liu, Ma, *Query selection techniques for efficient crawling of structured web sources*, ICDE 2006, tr. 47–56 (A).
- Wang, Lin, Metzler, *A cascade ranking model for efficient ranked retrieval*, SIGIR 2011, tr. 105–114 (A). Nogueira et al., *Multi-stage document ranking with BERT*, arXiv:1910.14424 (A).

**Nội bộ:** `r1_memory_architecture.md`, `r2_youcom_dedup.md` (cùng thư mục), `DESIGN_DONE_LEDGER_2026-10-02.md`, `TODO_QA_SCOUT_2026-10-02.md`, `research_policies/general_angles.v1.json`.
