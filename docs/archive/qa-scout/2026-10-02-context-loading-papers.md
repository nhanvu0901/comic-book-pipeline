# R3 - Văn liệu về cách nạp / điều kiện hoá context cho search agent (scout You.com)

Ngày: 2026-10-02. Phạm vi: bộ nhớ agent, nén context, vòng lặp search agent, ràng buộc phủ định, và tổng hợp cho scout.

**Nhãn bằng chứng**
- **[P]** = bài báo cho thấy. Tôi tải PDF từ arXiv và đọc nguyên văn; mọi số lấy từ text PDF. (Công cụ fetch tóm tắt PDF từng trả sai cả tên bài, nên tôi không dùng nó cho số liệu.)
- **[D]** = tài liệu/blog chính thức của vendor, số tự báo cáo, chưa qua bình duyệt.
- **[R]** = bằng chứng trong repo của mình.
- **[S]** = suy luận của tôi, không bài nào đo trực tiếp.
- Dấu † sau venue = lấy từ trí nhớ, chưa đối chiếu lại. Venue không có dấu † đã thấy trong chính PDF.

---

## 0. Kết luận nhanh

1. **Nạp text không tốn tiền.** Research API tính theo lượt và bậc effort (lite $12, standard $50, deep $100, exhaustive $450 mỗi 1.000 lượt), không theo token [D: you.com/docs/administration/billing]. Thêm hay bớt 2,5k ký tự không đổi hoá đơn, nên nén prompt kiểu LLMLingua không có lợi tiền ở đây [S]. Câu hỏi đúng là **hiệu lực**: brief tránh có làm giảm lặp thật không.
2. **Bộ nhớ: lát cắt nhỏ, liên quan thắng nhồi toàn bộ.** Zep: 1,6k token cho 71,2% so với 115k token cho 60,2% (gpt-4o, LongMemEval) [P]. Mem0: giảm hơn 90% token nhưng full-context vẫn hơn 6 điểm J (72,9 so với 66,9) [P]. Kế hoạch hiện tại (15 đến 25 mục, tối đa 2,5k ký tự) khớp đồng thuận này.
3. **Kiến trúc "bộ nhớ ở phía mình, agent hỏi như tool" (MemGPT, memory tool Anthropic, Search-R1) cần receiver gọi được tool của mình.** You.com Research là hộp đen: docs không có tham số tool, phiên hay bộ nhớ cho người gọi [D]. Chỉ có hai đường giữ bộ nhớ ở phía mình: post-filter bằng code, hoặc tự dựng vòng lặp với You.com Search làm tool [S].
4. **Chỉ phương pháp text-vào text-ra dùng được với receiver không fine-tune.** Gist, ICAE, AutoCompressors, xRAG, Prompt Cache, CacheBlend đều cần chạm vào trọng số, embedding hoặc KV cache của model nhận [P].
5. **Search agent trong văn liệu hầu như không có visited-set tường minh**: trạng thái = transcript + trần hành động + token `<answer>` [P]. Lặp tool call là lỗi thật (Kimi lặp 42,6% bước trên BrowseComp-Plus [P]); hệ 2025-26 (SLIM, AdaCoM) sửa bằng bộ quản lý context bên ngoài ghi "ứng viên đã loại + query vô ích". Đó là hình dạng của "ledger ở ngoài, đưa lát cắt vào".
6. **Phủ định là điểm yếu có số đo, nhưng brief của mình nằm ở vùng ít rủi ro.** Pink-elephant: được dặn tránh, model yếu vẫn nhắc ngang hoặc hơn tỉ lệ nền (0,33 lên 0,36), GPT-4 còn 0,13 [P]. Các hiệu ứng độ dài được đo ở hàng nghìn đến chục nghìn token; brief 25 mục khoảng 600 đến 900 token [S].
7. **Không bài nào đo đúng bài toán của mình** (search agent hộp đen, danh sách tránh phía người gọi, mục tiêu liệt kê mục mới). Gần nhất là NoveltyBench: đưa lại câu trả lời trước vào context rồi bảo "khác đi" đạt đa dạng ngang người ở 8 lần sinh [P]. Bằng chứng nội bộ [R]: code ghi You.com trả lại `Immortal Hulk #1` và `Venom #13` dù cả hai nằm trong `--have` (run micro 2026-08-05); đó là giai thoại 2 mục, chưa phải tỉ lệ đo.
8. **Xếp hạng [S]:** (1) Hybrid: steering dương theo phần bù của ledger + over-generate + post-filter + phản hồi va chạm + brief rút gọn; (2) Zero-text làm nhánh đối chứng; (3) Steering dương đứng riêng; (4) Vòng lặp cục bộ (leo thang); (5) Tóm tắt lane đứng riêng. Chi tiết mục 5.
9. **Chốt bằng thực nghiệm rẻ (khoảng $1 đến $3):** 4 nhánh brief, đo tỉ lệ va chạm sau post-filter (mục 5.4).

---

## 1. Kiến trúc bộ nhớ cho agent (Q1)

| Hệ thống (venue) | Lưu gì | Cái gì vào context | Số đo [P] | Chi phí |
|---|---|---|---|---|
| **MemGPT** (Packer et al., arXiv 2310.08560) | Main context + recall storage + archival storage | LLM tự gọi function để search/page; cảnh báo "memory pressure" ở 70% cửa sổ, evict ở 100% | DMR: GPT-4 32,1% thành 92,5%; GPT-4 Turbo 35,3% thành 93,4%; GPT-3.5 38,7% thành 66,9% | Nhiều lượt LLM; tác giả ghi agent "thường dừng phân trang trước khi hết kết quả" |
| **Generative Agents** (Park et al., UIST 2023) | Memory stream ngôn ngữ tự nhiên + reflection | Top-ranked vừa cửa sổ; điểm = recency + importance + relevance, mỗi alpha = 1, min-max, decay 0,995 | TrueSkill: full 29,89; bỏ reflection 26,88; không memory 21,21 | "Hàng nghìn đô token" cho 25 agent trong 2 ngày |
| **Reflexion** (Shinn et al., NeurIPS 2023†) | Phản hồi bằng lời về lần thử thất bại | Chỉ Ω = 1 đến 3 reflection gần nhất | AlfWorld +22%, HotPotQA +20%, HumanEval +11% (pass@1 91% so với GPT-4 80%) | Thêm một lượt LLM tự phản hồi |
| **MemoryBank** (Zhong et al., AAAI 2024†) | Hội thoại + tóm tắt, cường độ nhớ S | Truy hồi theo tương tự; quên theo R = e^(-t/S), S tăng 1 khi nhắc lại | Chủ yếu định tính | Thấp |
| **A-MEM** (Xu et al., arXiv 2502.12110) | Note kiểu Zettelkasten, LLM sinh liên kết và "tiến hoá" note cũ | Top-k = 10 theo embedding + liên kết | Hơn MemGPT/MemoryBank/ReadAgent trên LoCoMo, DialSim | 1.200 đến 2.500 token mỗi thao tác so với 16.900 (giảm 85 đến 93%, tự báo) |
| **Mem0** (Chhikara et al., arXiv 2504.19413) | Fact trích bằng LLM; ADD/UPDATE/DELETE/NOOP | Top-k ngữ nghĩa, ~1.764 token | J = 66,88 (Mem0), 68,44 (Mem0g); full-context 72,90 với 26.031 token | p95 tổng 1,44 s so với 17,12 s; giảm hơn 90% token |
| **HippoRAG** (Gutiérrez et al., NeurIPS 2024†) | KG từ OpenIE + index kiểu hippocampus | Personalized PageRank từ thực thể trong query | Tới +20% multi-hop QA | Một bước; rẻ hơn 10-20 lần (abstract; thân bài ghi 10-30) và nhanh hơn 6-13 lần so với IRCoT |
| **Zep / Graphiti** (Rasmussen et al., arXiv 2501.13956) | KG thời gian hai chiều; cạnh bị vô hiệu hoá có dấu thời gian | cosine + BM25 + BFS, rerank RRF/MMR | DMR 94,8% so với 93,4%; LongMemEval gpt-4o 71,2% so với 60,2%; gpt-4o-mini 63,8% so với 55,4% | 2,58 s so với 28,9 s; 1,6k so với 115k token |
| **MemoRAG** (Qian et al., WWW 2025†) | Memory toàn cục dạng KV nén của model nhẹ | Model nhẹ sinh bản nháp/gợi ý dẫn truy hồi, model đắt sinh đáp án | Tôi chưa xác minh số | Kiến trúc hai hệ |
| **Khảo sát** (Zhang et al., 2404.13501; Hu et al., 2512.13564) | - | Khung ghi / quản lý / đọc; dạng token, tham số, tiềm ẩn | - | - |

**Bài học cho ledger "đã làm"**
- **Chọn lọc có rủi ro bỏ sót.** Mem0 thừa nhận full-context vẫn nhỉnh. Với ledger, bỏ sót = lặp item cũ, nên post-filter bằng code là tầng bắt buộc, brief chỉ là phụ [S].
- **"Quên" và "tiến hoá bằng LLM" không hợp với sổ cái.** MemoryBank cố ý quên; Mem0 và A-MEM để LLM quyết định UPDATE/DELETE hoặc viết lại note. "Đã sản xuất" phải là sự thật tuyệt đối [S]. Điểm đáng mượn là Zep: vô hiệu hoá cạnh có dấu thời gian thay vì xoá, tương ứng sự kiện `unbanned`/`expires` trong thiết kế ledger.
- **Điểm truy hồi nhiều thành phần (Generative Agents) dùng được để chọn 15 đến 25 mục cho brief:** relevance (khớp thực thể/lane) + recency + độ nghiêm trọng (banned > produced > rejected) [P, S].
- **Reflexion là tiền lệ trực tiếp cho "phản hồi va chạm":** chỉ giữ 1 đến 3 phản hồi gần nhất về lần thử trước, lần sau tốt hơn rõ rệt [P].
- **Agent tự quyết đọc memory có thể truy hồi thiếu** (MemGPT dừng sớm [P]). Nếu tự dựng vòng lặp, kiểm ledger phải là cổng bằng code, đừng trông vào việc LLM tự gọi tool [S].

---

## 2. Nén context và nạp context hiệu quả (Q2)

| Phương pháp (venue) | Cơ chế | Số đo [P] | Dùng được với You.com? |
|---|---|---|---|
| **LLMLingua** (Jiang et al., EMNLP 2023†) | LM nhỏ lọc token | Nén tới 20 lần, ít mất hiệu năng | Về kỹ thuật có (đầu ra là text; bài báo nhắm LLM chỉ qua API). Không đáng: billing theo lượt; rủi ro bỏ token làm hỏng số issue [S] |
| **LongLLMLingua** (ACL 2024†) | Nén biết câu hỏi, chống position bias | NQ +21,4% với khoảng 4 lần ít token; giảm 94,0% chi phí LooGLE; nhanh 1,4-2,6 lần ở prompt 10k | Như trên |
| **LLMLingua-2** (Pan et al., ACL Findings 2024†) | Phân loại token bằng encoder, chưng cất từ LLM | Nhanh hơn 3-6 lần LLMLingua; e2e nhanh 1,6-2,9 lần ở nén 2-5 lần | Như trên |
| **Selective Context** (Li et al., EMNLP 2023†) | Cắt đơn vị có self-information thấp | Giảm bộ nhớ và latency, chất lượng tương đương | Như trên |
| **RECOMP** (Xu, Shi, Choi, ICLR 2024†) | Bộ nén trích xuất/tóm tắt; **trả chuỗi rỗng nếu không liên quan** | Nén xuống 6% với mất ít; bộ nén chuyển được sang LM khác | **Ý tưởng dùng được:** không đưa brief nếu không có mục liên quan |
| **xRAG** (Cheng et al., NeurIPS 2024†) | Chiếu embedding thành một token vào LM | +10% trung bình; giảm 3,53 lần FLOPs | Không: cần đưa embedding vào đầu vào model |
| **AutoCompressors** (Chevalier et al., EMNLP 2023†), **Gist** (Mu et al., NeurIPS 2023†), **ICAE** (Ge et al., ICLR 2024) | Fine-tune LM để nén thành vector/token đặc biệt | Gist: nén 26 lần, giảm tới 40% FLOPs; ICAE: nén 4 lần; AutoCompressors: tới 30.720 token | Không: phải fine-tune receiver |
| **Prompt Cache** (Gim et al., MLSys 2024†), **CacheBlend** (Yao et al., EuroSys 2025†) | Dùng lại attention/KV state ở server suy luận | TTFT nhanh 8 lần (GPU) đến 60 lần (CPU) | Không: phía server của nhà cung cấp |
| **Prefix caching của vendor** (docs OpenAI, Anthropic) | Đọc lại tiền tố khớp từng byte | Anthropic: đọc 0,1 lần giá input, ghi 1,25 lần, TTL 5 phút [D] | Không có tài liệu cho You.com [D, S] |

**Diễn giải cho scout**
- **Hai cột chặn:** trọng số/KV không chạm tới được; You.com tính theo lượt nên token tiết kiệm = 0 đồng [D, S]. Giá trị của họ bài báo nén nằm ở **ý tưởng**, không phải công cụ.
- **Ý tưởng đáng mượn:** RECOMP "chọn lọc, không liên quan thì rỗng" [P]. Nếu lượt scout không có mục liên quan (điểm khớp dưới ngưỡng), brief nên rỗng thay vì lấp 15 mục xa chủ đề, vì thông tin không liên quan làm giảm mạnh độ chính xác (Shi et al., ICML 2023†; 2302.00093) [P].
- **Rủi ro riêng của nén mất mát:** "Venom #13" và "Venom #1" khác nhau một chữ số [R]. Không bài nào đo tác động của nén token lên định danh có số [S]. Nếu cần rút gọn, dùng **mã hoá có cấu trúc tự viết** (gộp dải số issue theo series), không dùng nén token.
- **Cache tiền tố** chỉ áp dụng nếu tự gọi LLM của vendor (phương án 4): phần tĩnh ở đầu, phần động ở cuối [D].
- **You.com tự nén context bên trong**: docs mô tả "context-masking and compaction" cho lượt chạy tới hơn 1.000 lượt suy luận và 10 triệu token [D]. Docs không nói prompt gốc có giữ nguyên qua nén hay không [S]; rủi ro là chỉ dẫn phủ định ở đầu bị tóm gọn.

---

## 3. Vòng lặp search mà bộ nhớ ở phía mình (Q3)

| Hệ | Trạng thái "đã biết/đã thấy" | Dedup | Dừng |
|---|---|---|---|
| **ReAct** (Yao et al., ICLR 2023) | Chỉ transcript | Không. Lặp suy nghĩ/hành động là lỗi đặc trưng ("reasoning error" gồm lặp = 47% trong 50 quỹ đạo thất bại mẫu) | Trần 7 bước (HotpotQA) / 5 (FEVER), rồi lùi về CoT-SC |
| **Self-Ask** (Press et al., Findings EMNLP 2023†) | Chuỗi "Follow up / Intermediate answer" | Không | Model tự ra câu trả lời cuối |
| **IRCoT** (Trivedi et al., ACL 2023†) | Tập đoạn văn đã truy hồi, hiện toàn bộ trong prompt | Gộp theo tập | "answer is" hoặc tối đa 4 bước |
| **FLARE** (Jiang et al., EMNLP 2023†), **Self-RAG** (Asai et al., ICLR 2024†) | Không có trạng thái toàn cục | Không | FLARE: truy hồi khi token sắp sinh có xác suất dưới ngưỡng θ; Self-RAG: reflection token quyết định |
| **Search-o1** (arXiv 2501.05366) | Transcript; **Reason-in-Documents** cô đọng tài liệu trước khi chèn | Không thấy cơ chế tường minh | Model tự kết thúc |
| **Search-R1** (Jin et al., COLM 2025), **R1-Searcher**, **ReSearch** | Giao thức thẻ `<search>`/`<result>`; token truy hồi bị mask khi huấn luyện | Không thấy cơ chế tường minh | Ngân sách hành động B = 4, top-3 đoạn, hoặc `<answer>` |
| **DeepResearcher** (arXiv 2504.03160) | Agent duyệt web giữ **short-term memory theo từng query**, tự quyết đọc tiếp hay dừng | Top-k cố định (10) | Agent duyệt tự dừng; hệ ra `<answer>` |
| **WebThinker** (arXiv 2504.21776) | **Document memory** của mọi trang đã khám phá, cấp cho tool viết | Chỉ xử lý đoạn trùng trong báo cáo | EOS hoặc hết token |
| **WebWalker** (arXiv 2501.07572), **WebDancer** (arXiv 2505.22648) | WebWalker: critic agent giữ memory; WebDancer: lịch sử H_t + summarizer | Không | Critic quyết định hoặc hết bước tối đa; hành động `answer` |
| **MindSearch** (Chen et al., ICLR 2025) | DAG sub-question của WebPlanner; WebSearcher tóm tắt | ReAct lãng phí query tìm lại cùng từ khoá (3,5 so với 3,2 query trung bình) | Sau vài lần thất bại thì sinh node trả lời để thoát vòng lặp |
| **STORM** (NAACL 2024†), **Co-STORM** (Jiang et al., EMNLP 2024†) | STORM: câu hỏi mới sinh **dựa trên lịch sử hội thoại**; Co-STORM: mind map, moderator dùng **thông tin truy hồi nhưng chưa trích** | Co-STORM rerank bằng cos(i, topic)^α x (1 - cos(i, câu hỏi đã hỏi))^(1-α); có rubric "No Repetition" | Tối đa M vòng / người dùng |
| **Anthropic multi-agent research** [D] | Lead lưu **kế hoạch vào Memory** vì cửa sổ 200k token bị cắt | Mỗi subagent cần "mục tiêu, định dạng, hướng dẫn nguồn và **ranh giới nhiệm vụ rõ**", nếu không "agents duplicate work" | Quy tắc quy mô nỗ lực ghi trong prompt (1 agent 3-10 lệnh gọi; so sánh 2-4 subagent; phức tạp hơn 10) |
| **OpenAI deep research card, Gemini Deep Research** | Tài liệu tôi đọc không mô tả visited-state/dedup (card OpenAI không trích được text; Gemini chỉ qua tóm tắt thứ cấp: "shared state giữa planner và task model") | - | - |

**Hệ 2025-26 nhắm đúng vấn đề lặp**
- **SLIM / Lost in the Maze** (Yen et al., COLM 2026; 2510.18939) [P]: tách search/browse và tóm tắt quỹ đạo định kỳ; với o3 đạt 56% BrowseComp, 33% HLE, **ít lệnh gọi tool hơn 4 đến 6 lần**.
- **AdaCoM** (Yi et al., arXiv 2605.30785) [P]: LLM bên ngoài quản lý context của agent **đóng nguồn/đóng băng**; trạng thái gồm "yêu cầu, ràng buộc chưa giải quyết, bằng chứng, hướng đi, **ứng viên bị loại và query vô ích**". Kimi vanilla lặp 42,6% bước tool trên BrowseComp-Plus.
- **ReSum** (arXiv 2509.13313): +4,5% so với ReAct không huấn luyện; **MEM1** (arXiv 2506.15841): hiệu năng gấp 3,5 lần, bộ nhớ ít hơn 3,7 lần [P].
- **Memory tool Anthropic** [D]: bộ nhớ ở phía ứng dụng, "just-in-time retrieval"; blog báo memory tool + context editing cải thiện 39% trên đánh giá agentic search nội bộ, giảm 84% token ở workflow 100 lượt (tự báo).

**Nhận định cho scout [S]**
1. Trạng thái trong các bài báo là **trong một lượt chạy**. Không bài nào quản lý bộ nhớ "đã sản xuất" xuyên lượt cho tác vụ liệt kê mục mới. Ledger của mình là bài toán khác, nên các hệ trên cho ý tưởng chứ không cho đáp án.
2. Sửa lặp = bộ quản lý bên ngoài ghi "đã thử". AdaCoM được sửa context của agent, còn mình chỉ có prompt đầu vào.
3. Dừng: mọi hệ dùng trần cứng (4, 5, 7 bước) cộng tự khai báo xong. Với liệt kê: "đủ N mục mới sau post-filter hoặc tối đa R = 2 đến 3 vòng".
4. Co-STORM chọn hướng mới bằng **tương đồng chủ đề và bất tương đồng với cái đã làm**, không bằng danh sách cấm. Dùng được để chọn facet tìm kiếm (phương án 3).

---

## 4. Ràng buộc phủ định trong prompt (Q4)

| Hiện tượng | Bài báo | Số |
|---|---|---|
| Vị trí: đầu/cuối tốt, giữa kém | **Lost in the Middle** (Liu et al., TACL 2024†; 2307.03172) | Đường cong chữ U; GPT-3.5-Turbo với đoạn đúng ở giữa còn **thấp hơn** closed-book (56,1%) |
| Độ dài | **RULER** (Hsieh et al., COLM 2024) | 17 model; chỉ khoảng một nửa giữ hiệu năng chấp nhận được ở 32K |
| Không khớp mặt chữ | **NoLiMa** (Modarressi et al., ICML 2025) | 13 model; ở 32K, 11 model dưới 50% mức ngắn; GPT-4o 99,3% xuống 69,7% |
| Context rot | **Chroma "Context Rot"** (Hong, Troynikov, Huber, 2025) [D, báo cáo kỹ thuật] | 18 LLM; giảm theo độ dài kể cả tác vụ đơn giản; query-tài liệu ít tương đồng ngữ nghĩa thì giảm nhanh hơn; một distractor đã đủ gây hại; LongMemEval: prompt tập trung ~300 token tốt hơn rõ rệt prompt đầy đủ ~113k token |
| Mật độ chỉ dẫn | **IFScale** (Jaroslawicz et al., 2507.11538) | 20 model; tốt nhất 68% ở 500 chỉ dẫn; ba kiểu suy giảm (ngưỡng: o3, gemini-2.5-pro; tuyến tính: gpt-4.1, claude-sonnet-4; mũ: gpt-4o); thiên vị chỉ dẫn đầu; lỗi chủ yếu là bỏ sót. **Chỉ dẫn là "bao gồm từ khoá" (khẳng định), không phải cấm** |
| Pink elephant | **Castricato et al.** (2402.07896) | Tỉ lệ nhắc khi được dặn tránh (nền 0,33): OpenHermes-7B 0,36; 13B 0,34; Llama-2-13B-Chat 0,25; GPT-4 0,13. Bộ test thiết kế để dễ kích hoạt |
| Điều cấm phai theo độ sâu | **Gamage** (2604.20911; preprint một tác giả) | 12 model, 4.416 lượt; với Mistral Large 3, tuân thủ điều cấm 73% (lượt 5) xuống 33% (lượt 16), điều bắt buộc giữ 100%; **nhắc lại ràng buộc trước "Safe Turn Depth" khôi phục tuân thủ** |
| Nhớ mà vẫn vi phạm | **DriftBench** (Kruthof, 2604.28031; preprint một tác giả) | "Knows-but-violates" 8% đến 99% tuỳ model; 4 trên 7 model vượt 50% |
| Truy hồi không hiểu phủ định | **NevIR** (Weller et al., 2305.07614), **ExcluIR** (Zhang et al., 2404.17288) | NevIR (xếp hạng hai văn bản chỉ khác phủ định): ngẫu nhiên 25%; TF-IDF 2,0%; bi-encoder 6,8-11,1%; ColBERT 13,0-19,7%; cross-encoder 22,4-50,6% |

**Điều này nói gì về brief của mình**
- **Kích thước brief ngoài vùng rủi ro của hầu hết các bài [S].** 15 đến 25 mục (2,5k ký tự) khoảng 600 đến 900 token; các hiệu ứng độ dài trên đo ở vài nghìn đến vài chục nghìn token. IFScale cho thấy model lập luận gần hoàn hảo tới một ngưỡng mật độ, nhưng đó là chỉ dẫn khẳng định nên là ngoại suy.
- **Rủi ro lớn hơn nằm ở agent nhiều lượt bên trong You.com [S].** Agent tự nén context [D]; "điều cấm phai theo độ sâu" và "nhắc lại thì phục hồi" [P, preprint] gợi ý chỉ dẫn phủ định ở lượt 0 yếu dần. Hệ quả kiểm chứng được: effort thấp hơn và nhiều lượt gọi ngắn song song có thể giữ tuân thủ tốt hơn một lượt deep dài. Chưa có số đo cho You.com.
- **"Nhắc tên là mồi" chưa có bài đo trực tiếp [S].** Pink-elephant cho thấy dặn tránh có thể tăng xác suất nhắc; với agent search, giả thuyết tương ứng là tên item trong brief lọt vào query tìm kiếm và kéo chính trang đó về. NevIR/ExcluIR cho thấy máy truy hồi coi "không X" gần như "X" [P]. Cả hai là bằng chứng gián tiếp.
- **Bằng chứng ngược chiều: danh sách nhỏ có thể hiệu quả.** NoveltyBench (Zhang et al., COLM 2025; 2504.05228): giữ các câu trả lời trước trong context và yêu cầu "khác" đưa Claude 3 Opus, Gemini 2.0 Pro, GPT-4o về mức đa dạng ngang người ở 8 lần sinh; paraphrase và system prompt "hãy độc đáo" chỉ hiệu quả nhẹ [P]. Đặc điểm: câu trả lời ngắn, ít mục, model thấy chính cái nó đã nói. Tương ứng với mình: **chuỗi gọi nối tiếp, mỗi lần chỉ cho thấy các va chạm vừa xảy ra** (phương án 1).
- **Vị trí [S, nội suy từ chữ U + primacy]:** luật cứng ở đầu, brief và một dòng nhắc ở cuối. Thiết kế hiện tại đã đưa luật cứng lên đầu sau lỗi bị cắt ở 9.000 ký tự [R].
- **Hậu lọc bằng code là chuẩn khi cần đảm bảo cứng:** Self-Instruct chỉ giữ chỉ dẫn mới nếu ROUGE-L so với mọi cái đã có nhỏ hơn 0,7 (Wang et al., ACL 2023; 2212.10560) [P].

---

## 5. Tổng hợp cho scout (Q5)

Giá: Research standard $0,05, deep $0,10, Search $0,005 mỗi lượt [D]. Chi phí LLM của mình là [S] vì tôi chưa xác minh giá model hiện hành.

**Trả lời thẳng câu hỏi "lưu rồi nạp text mỗi lần có kém hiệu quả không?"**
- Tiền: không (giá theo lượt, prompt 4k hay 40k ký tự như nhau [D]). Latency: không đáng kể, vì agent bên trong xử lý tới hàng triệu token [D, S].
- Hiệu lực: **chưa biết, và là chỗ cần đo.** Điều quyết định không phải kích thước brief mà là brief có giảm tỉ lệ va chạm so với không brief hay không [S].
- Lãng phí có thể bỏ ngay: dán brief khi không có mục liên quan (RECOMP: để rỗng), dán mục xa chủ đề (nhiễu), dán tên item bằng câu phủ định nổi bật [S].

### 5.1 Năm phương án thay "dán avoid list vào prompt", xếp hạng

**Hạng 1. Hybrid (steering dương + over-generate + post-filter + phản hồi va chạm + brief rút gọn)**
- Làm gì: (i) từ ledger tính **phần bù** (năm chưa scout, series/lane còn thưa, nhân vật chưa dùng) và gửi các lượt `standard` song song, mỗi lượt một facet (docs You.com có ví dụ "four parallel Research calls", examples/biomedical-research.md [D]); (ii) xin 15 ứng viên mỗi lượt; (iii) post-filter bằng khoá cấu trúc; (iv) nếu thiếu mục mới, gọi vòng 2 mà **chỉ** nêu các va chạm vừa xảy ra (1 đến 5 mục); (v) giữ brief nhưng rút còn khoảng 0,5k đến 1k ký tự mục liên quan nhất, đặt cuối prompt.
- Bằng chứng: AttrPrompt, prompt theo thuộc tính khớp hiệu năng với 5% chi phí truy vấn và đa dạng hơn (NeurIPS 2023 D&B; 2306.15895) [P]; Co-STORM [P]; "ranh giới nhiệm vụ rõ" của Anthropic [D]; Reflexion và NoveltyBench cho cơ chế phản hồi [P]; mồi dương ngẫu nhiên tăng đa dạng (2601.18053, nghiên cứu nhỏ) [P, yếu].
- Suy luận: hiệu quả thực của steering dương lên va chạm với You.com; ngưỡng rút gọn brief [S].
- Chi phí mỗi lượt scout: 3 standard song song = $0,15, vòng 2 có điều kiện (+$0,05), **khoảng $0,15 đến $0,25**; latency 30 đến 60 giây (+10-30 giây nếu có vòng 2) [S]. Cỡ triển khai: nhỏ (facet generator khoảng 100-200 dòng, vòng phản hồi khoảng 50-80 dòng; post-filter đã có trong kế hoạch) [S].
- Rủi ro: facet quá hẹp làm hụt ứng viên; bỏ brief nhỏ có thể tăng va chạm (cần A/B); steering theo facet có thể lệch chất lượng xếp hạng "best-of".

**Hạng 2. Zero-text: over-generate + post-filter thuần**
- Làm gì: không đưa danh sách "đã làm"; xin nhiều hơn cần (15 để lấy 5); lọc bằng khoá cấu trúc; tối đa R = 2 vòng.
- Bằng chứng: Self-Instruct và hậu lọc [P]; pink-elephant cho thấy nhắc tên có thể phản tác dụng [P]; giá theo lượt nên xin nhiều không tốn thêm [D].
- Suy luận: va chạm sẽ cao nếu search engine luôn trả cùng top "best-of" mỗi lần; mỗi va chạm là một slot lãng phí [S].
- Chi phí: $0,05 (1 standard) đến $0,15 (3 song song); 10 đến 30 giây. Cỡ triển khai: gần 0. **Nhánh đối chứng bắt buộc trong A/B.**

**Hạng 3. Steering dương đứng riêng ("tìm ở đâu", không "tránh gì")**
- Như hạng 1 nhưng bỏ phản hồi va chạm và bỏ brief: chỉ cho biết **vùng cần tìm** (năm, lane còn thưa, bộ sưu tập chưa khai thác).
- Bằng chứng: như hạng 1 [P/D]. Suy luận: chặn lặp yếu hơn hạng 1 vì facet khác không chắc loại item cũ [S].
- Chi phí: $0,15; cỡ triển khai nhỏ; cần metadata ledger đủ giàu (năm, series).

**Hạng 4. Vòng lặp cục bộ: You.com Search làm tool, ledger là cổng bằng code**
- Làm gì: tự chạy agent (LLM của mình) với tool `search` ($0,005 mỗi lượt) và cổng ledger; danh sách "đã làm" không bao giờ gửi cho You.com.
- Bằng chứng ủng hộ: Search-R1, MemGPT, WebThinker, memory tool Anthropic cho kiến trúc; AdaCoM/SLIM cho giá trị ghi "đã thử/ứng viên bị loại" ngoài agent [P]. Bằng chứng chống: ReAct lặp, Kimi lặp 42,6% [P]; multi-agent dùng khoảng 15 lần token của chat [D]; mất đối chiếu đa nguồn mà Research làm sẵn [S].
- Chi phí: Search 10-30 lượt = $0,05-0,15 cộng token LLM của mình, **khoảng $0,15 đến $0,60** [S]; latency 1-3 phút; cỡ triển khai lớn (400-800 dòng, harness đánh giá, chống prompt-injection từ web).
- Khi dùng: leo thang, chỉ khi hạng 1 vẫn còn va chạm cao.

**Hạng 5. Tóm tắt lane / bộ nhớ nén (kiểu RECOMP, không phải LLMLingua)**
- Làm gì: thay danh sách mục bằng thống kê ("Batman/Q&A đã làm 14 câu; lane bão hoà: Absolute Batman (8)").
- Bằng chứng: RECOMP, community summaries của Zep, compaction của Anthropic [P/D]; không bài nào đo việc tóm tắt chặn lặp item cụ thể, và tóm tắt không nêu được "Final Crisis #6".
- Suy luận: giá trị chính là **tín hiệu steering**, đã nằm trong hạng 1 và 3; đứng riêng chặn lặp yếu, dễ tạo ảo giác "đã tránh" [S].
- Chi phí: $0 thêm; 1k ký tự; cỡ triển khai nhỏ.

### 5.2 Bảng tóm tắt

| Phương án | Chặn lặp (dự kiến) | $ mỗi lượt | Latency | Triển khai | Rủi ro chính | Độ chắc |
|---|---|---|---|---|---|---|
| 1. Hybrid | Cao nhất | $0,15-0,25 | 30-60 s | Nhỏ | Facet hẹp; cần A/B bỏ brief | Suy luận, nền gián tiếp |
| 2. Zero-text | Trung bình | $0,05-0,15 | 10-30 s | Rất nhỏ | Slot lãng phí | Suy luận |
| 3. Steering dương | Trung bình | $0,15 | 30-60 s | Nhỏ | Cần metadata ledger | Gián tiếp (AttrPrompt, Co-STORM) |
| 4. Vòng lặp cục bộ | Cao (cổng cứng) nhưng chất lượng tổng hợp có thể giảm | $0,15-0,60 | 1-3 phút | Lớn | Lặp tool, mất kiểm chứng đa nguồn | Trực tiếp cho kiến trúc, không cho bài toán này |
| 5. Tóm tắt lane | Thấp đứng riêng | $0 thêm | 0 | Nhỏ | Ảo giác an toàn | Suy luận |

### 5.3 Bằng chứng so với suy luận

**Bằng chứng [P/D]:** context nhỏ chọn lọc thắng nhồi toàn bộ (Zep, Mem0, A-MEM, HippoRAG, Context Rot); nén học được và KV cache cần truy cập model nhận; lặp tool call là lỗi thật và trần số bước cứng là chuẩn; phủ định yếu hơn khẳng định (pink elephant, prohibition decay, truy hồi không hiểu phủ định); in-context regeneration hiệu quả với model mạnh và danh sách ngắn (NoveltyBench); You.com Research tính theo lượt, chỉ có `exclude_domains` chứ không loại trừ theo URL/thực thể [D, R2].

**Suy luận [S]:** brief 2,5k ký tự nằm ngoài vùng suy giảm đo được và rủi ro nằm ở agent nhiều lượt; nhắc tên item có thể mồi query tìm kiếm; thứ hạng 1 > 2 > 3 > 4 > 5 và mọi con số chi phí/latency của hạng 1 và 4; effort thấp với nhiều lượt ngắn giữ tuân thủ phủ định tốt hơn một lượt deep.

### 5.4 Thí nghiệm A/B rẻ để chốt (khoảng $1 đến $3) [S]

- **Bốn nhánh** trên cùng tập truy vấn: (A0) không brief; (A1) brief hiện tại 15-25 mục, 2,5k ký tự, ở đầu; (A2) brief rút còn khoảng 800 ký tự, đặt cuối; (A3) steering dương theo phần bù, không brief.
- **Chỉ số chính:** tỉ lệ va chạm = ứng viên bị post-filter loại vì trùng ledger / tổng ứng viên trả về (trước lọc). Phụ: số mục mới sau lọc trên mỗi $, độ trễ.
- **Cỡ mẫu:** mỗi nhánh khoảng 6 lượt standard (4 x 6 x $0,05 = $1,2), đủ khoảng 60-90 ứng viên mỗi nhánh để thấy chênh lớn (ví dụ 30% so với 10%); chạy thêm nếu chênh nhỏ.
- **Quy tắc quyết định:** nếu A1 và A2 không giảm va chạm có ý nghĩa so với A0, bỏ brief (dùng A0/A3). Nếu giảm, giữ phiên bản ngắn nhất đạt hiệu quả.
- **Cần ledger có dữ liệu thật;** nếu còn rỗng, nạp trước từ `comic_candidates.csv`, `qa_question_banlist.md`.

---

## 6. Hạn chế và những gì chưa xác minh

- Không bài nào đo search agent hộp đen với danh sách tránh ở phía người gọi; mọi áp dụng cho You.com là [S]. Model bên trong You.com, cách nó giữ prompt gốc khi nén context, và prefix caching đều không được công bố [D].
- Gamage (2604.20911) và Kruthof (2604.28031) là preprint một tác giả năm 2026, tác vụ là hội thoại DevOps/ideation chứ không phải search agent; số 73% sang 33% là của một model.
- Số của Mem0, Zep, A-MEM do chính nhóm làm hệ thống báo cáo; số của Anthropic (multi-agent 15 lần token, memory tool 39%/84%) là nội bộ vendor.
- MemoRAG: đã đọc kiến trúc, chưa trích số. OpenAI system card không trích được text qua công cụ; Gemini Deep Research chỉ có tóm tắt thứ cấp (ZenML).
- Venue có dấu † chưa đối chiếu lại. Giá model LLM cho phương án 4 chưa xác minh.

---

## 7. Tài liệu tham khảo

arXiv: `https://arxiv.org/abs/<id>`.

**Bộ nhớ agent**
1. Packer et al. *MemGPT: Towards LLMs as Operating Systems.* arXiv 2310.08560.
2. Park et al. *Generative Agents.* UIST 2023. arXiv 2304.03442.
3. Shinn et al. *Reflexion.* NeurIPS 2023†. arXiv 2303.11366.
4. Zhong et al. *MemoryBank.* AAAI 2024†. arXiv 2305.10250.
5. Xu et al. *A-MEM: Agentic Memory for LLM Agents.* arXiv 2502.12110.
6. Chhikara et al. *Mem0.* arXiv 2504.19413.
7. Gutiérrez et al. *HippoRAG.* NeurIPS 2024†. arXiv 2405.14831.
8. Rasmussen et al. *Zep: A Temporal Knowledge Graph Architecture for Agent Memory.* arXiv 2501.13956.
9. Qian et al. *MemoRAG.* WWW 2025†. arXiv 2409.05591.
10. Zhang et al. *A Survey on the Memory Mechanism of LLM-based Agents.* arXiv 2404.13501. Hu et al. *Memory in the Age of AI Agents.* arXiv 2512.13564.

**Nén context và caching**
11. Xu, Shi, Choi. *RECOMP.* ICLR 2024†. arXiv 2310.04408.
12. Jiang et al. *LLMLingua.* EMNLP 2023†. arXiv 2310.05736; *LongLLMLingua.* ACL 2024†. arXiv 2310.06839; Pan et al. *LLMLingua-2.* ACL Findings 2024†. arXiv 2403.12968.
13. Li et al. *Selective Context.* EMNLP 2023†. arXiv 2310.06201.
14. Cheng et al. *xRAG.* NeurIPS 2024†. arXiv 2405.13792.
15. Chevalier et al. *AutoCompressors.* EMNLP 2023†. arXiv 2305.14788. Mu et al. *Gist tokens.* NeurIPS 2023†. arXiv 2304.08467. Ge et al. *ICAE.* ICLR 2024. arXiv 2307.06945.
16. Gim et al. *Prompt Cache.* MLSys 2024†. arXiv 2311.04934. Yao et al. *CacheBlend.* EuroSys 2025†. arXiv 2405.16444.
17. OpenAI *Prompt caching* (developers.openai.com/api/docs/guides/prompt-caching); Anthropic *Prompt caching* (platform.claude.com/docs/en/docs/build-with-claude/prompt-caching); truy cập 2026-10-02 [D].

**Search agent**
18. Yao et al. *ReAct.* ICLR 2023. arXiv 2210.03629. Press et al. *Self-Ask.* Findings EMNLP 2023†. arXiv 2210.03350. Trivedi et al. *IRCoT.* ACL 2023†. arXiv 2212.10509.
19. Jiang et al. *FLARE.* EMNLP 2023†. arXiv 2305.06983. Asai et al. *Self-RAG.* ICLR 2024†. arXiv 2310.11511.
20. Li et al. *Search-o1.* arXiv 2501.05366. Jin et al. *Search-R1.* COLM 2025. arXiv 2503.09516. Song et al. *R1-Searcher.* arXiv 2503.05592. Chen et al. *ReSearch.* arXiv 2503.19470. Zheng et al. *DeepResearcher.* arXiv 2504.03160.
21. Li et al. *WebThinker.* arXiv 2504.21776. Wu et al. *WebWalker.* arXiv 2501.07572; *WebDancer.* arXiv 2505.22648. Chen et al. *MindSearch.* ICLR 2025. arXiv 2407.20183.
22. Shao et al. *STORM.* NAACL 2024†. arXiv 2402.14207. Jiang et al. *Co-STORM.* EMNLP 2024†. arXiv 2408.15232.
23. Yen et al. *Lost in the Maze (SLIM).* COLM 2026. arXiv 2510.18939. Yi et al. *AdaCoM.* arXiv 2605.30785. Wu et al. *ReSum.* arXiv 2509.13313. Zhou et al. *MEM1.* arXiv 2506.15841.
24. Anthropic: *How we built our multi-agent research system*; *Effective context engineering for AI agents* (anthropic.com/engineering); *Memory tool* (platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool); *Managing context* (claude.com/blog/context-management) [D].
25. You.com docs: guides/research.md, administration/billing.md, examples/biomedical-research.md (truy cập 2026-10-02) [D]. OpenAI *Deep Research System Card*; ZenML, tóm tắt bài nói của Google DeepMind về Gemini Deep Research (thứ cấp).

**Phủ định, context dài, đa dạng**
26. Liu et al. *Lost in the Middle.* TACL 2024†. arXiv 2307.03172. Hsieh et al. *RULER.* COLM 2024. arXiv 2404.06654. Modarressi et al. *NoLiMa.* ICML 2025. arXiv 2502.05167.
27. Hong, Troynikov, Huber. *Context Rot.* Chroma technical report, 2025. https://www.trychroma.com/research/context-rot
28. Jaroslawicz et al. *How Many Instructions Can LLMs Follow at Once? (IFScale).* arXiv 2507.11538.
29. Castricato et al. *Suppressing Pink Elephants with Direct Principle Feedback.* arXiv 2402.07896.
30. Gamage. *Omission Constraints Decay While Commission Constraints Persist in Long-Context LLM Agents.* arXiv 2604.20911 (preprint). Kruthof. *Models Recall What They Violate (DriftBench).* arXiv 2604.28031 (preprint).
31. Shi et al. *LLMs Can Be Easily Distracted by Irrelevant Context.* ICML 2023†. arXiv 2302.00093.
32. Weller et al. *NevIR.* arXiv 2305.07614. Zhang et al. *ExcluIR.* arXiv 2404.17288.
33. Zhang et al. *NoveltyBench.* COLM 2025. arXiv 2504.05228. Yu et al. *AttrPrompt.* NeurIPS 2023 D&B. arXiv 2306.15895. Wang et al. *Self-Instruct.* ACL 2023. arXiv 2212.10560. Agrawal, Goyal. *Addressing LLM Diversity by Infusing Random Concepts.* arXiv 2601.18053.
34. Repo: `stages/youcom_scout.py` (comment ở `_same_issue`, dòng 220); `DESIGN_DONE_LEDGER_2026-10-02.md`; `research/r2_youcom_dedup.md` (thư mục scratchpad).
