# R1 - Memory architecture for the comic scout ("never propose it again")

Date: 2026-10-02. Scope: the scout memory (done / rejected / in-progress) for micro, Q&A and recap modes.
Legend: **[D]** documented (link in Sources), **[L]** local evidence read in this repo, **[I]** my inference or recommendation, **[?]** unverified or uncertain.
No repository file was edited. The only experiment I ran was a throw-away micro-benchmark in the scratchpad (section 4).

---

## 0. Summary

1. The problem is not "agent memory" in the Mem0/Letta sense. Those systems exist to turn *unstructured chat* into recallable facts. Your items are *structured records with natural keys* (series, volume/year, issue, status). The right tool is a set-membership ledger plus a deterministic filter, not a memory framework. [I]
2. Long "do not propose X" lists in a prompt are a weak control. Context-rot, lost-in-the-middle, NoLiMa, multi-instruction-decay and negation/priming results all point the same way [D], and your own code already records that You.com returned items that were on the `--have` list [L]. Code-side post-filtering is the norm in comparable generate-then-dedupe systems (Self-Instruct, idea-generation studies, Zep entity resolution) [D].
3. Recommended architecture: **append-only JSONL ledger in git (per-machine shard files) as the system of record, a rebuildable in-memory/SQLite index, a canonical-key matching ladder, and a three-part prompt block** (fixed rules first, a tiny lane summary, a top-K relevant-exclusion brief of at most ~2.5k chars). The ledger can grow to 10^5 rows without changing prompt size. [I]
4. The authoritative defence is a code-side post-filter over every returned candidate, with an "ambiguous" band routed to the OpenRouter gate with only the top-3 ledger neighbours (retrieve-then-judge). [I, pattern documented in Zep/AI Scientist]
5. No embeddings are needed for the core. Dedup unit differs by mode: issue key (recap, micro), question text + answer-set overlap of issue keys (Q&A). Embeddings are only a candidate generator for paraphrased Q&A questions; if measured misses justify it, use an in-process static or ONNX model (model2vec ~30 MB, numpy only; fastembed/bge-small), not a service. [I]
6. Storage: JSONL-in-git beats SQLite-in-git because you have two machines and git as the sync path (binary merges, WAL files, cloud-sync corruption). SQLite is still excellent as a derived read index. If only one machine ever writes, SQLite as the canonical store is equally defensible. [D for SQLite constraints, I for the choice]
7. Identity: key = normalized series + series start year + issue number, with `vol`, legacy number, batcave reader ids and fandom slug stored as aliases. Never fuzzy-match issue numbers. Beware `token_set_ratio` (returns 100 on subset). [D]
8. Migration is five small phases; phase 0 (hard rules out of the truncated digest, per-mode digests) needs no ledger and can ship first.
9. Biggest uncertainties: how You.com's agent treats exclusion text internally, the sync topology between Windows and Mac, calibration of fuzzy thresholds on your real data, and Comic Vine/GCD licensing for a monetized channel.
10. Cost context: You.com deep research is about $0.10 per call and standard $0.05 [D], so "over-generate and filter, retry once" is cheap compared with carrying a 9k-char list in every prompt.

---

## 1. What the repo does today (read-only inspection) [L]

- `stages/youcom_scout.py::build_scouted_digest` concatenates produced questions (from `projects/*/answer_context.json`), ban-list rows (`qa_question_banlist.md`, 103 lines) and recap rows (`comic_candidates.csv`, 262 rows), then `digest[:9000]`. The HARD RULES block is at the *end*, so it is the part cut. `TODO_QA_SCOUT_2026-10-02.md` measured the digest at 11,894 chars, about 80% of the Q&A prompt, mostly 160 recap titles irrelevant to Q&A.
- `projects/` is git-ignored (`.gitignore`), and was empty on this Mac when I looked, so `_project_questions()` returned nothing here. Memory derived from a cleanable folder is lost by design.
- The code already contains an authoritative post-filter: `is_burned` (token containment >= 0.6 and >= 2 non-format shared tokens), `_same_issue` (series-token Jaccard >= 0.6 plus issue number must match), `_series_burned`. The code comment on `_same_issue` says: "You.com IGNORES the --have list in the prompt: the 2026-08-05 micro run returned Immortal Hulk #1 and Venom #13 straight back". That is the strongest piece of evidence in this report.
- TODO item 4: the exclusion list goes through the LLM planner, which rewrote it and once inverted it ("should be included"); *Final Crisis #6* appeared in 5 of 5 Batman runs; one issue pair was returned twice via two URLs.
- Existing identity code: `stages/research_scout/issue_identity.py` (regex `#N`, year, series normalisation) used for exact micro requests.
- Memory-shaped data is scattered: ban-list markdown (mixed Vietnamese/English free text), CSV with statuses, `qa_question_bank.md`, `.claude/memory/scout_*.md` lessons (git-tracked), `research_sessions/` audit logs (untracked).

---

## 2. Q1 - How production-grade agent memory works (2025-2026)

### 2.1 Systems compared

| System | What it stores | Read path: what reaches the prompt | Write path | Embeddings? | Mapping to a growing done-list |
|---|---|---|---|---|---|
| **Anthropic context engineering + memory tool** [D: effective-context-engineering; memory-tool docs] | Files under `/memories`, client-side, you choose the backend | Agent keeps *lightweight identifiers* and loads data just-in-time via tools; system prompt auto-adds "ALWAYS VIEW YOUR MEMORY DIRECTORY"; `view` truncates text at 16,000 chars and supports line ranges | Agent writes notes (create/str_replace/insert); docs advise capping file sizes and expiring stale files | No | Keep a small always-read index plus detail files fetched on demand. "Smallest set of high-signal tokens" is the stated goal; context rot is the stated reason. |
| **OpenAI Agents SDK cookbooks** [D] | A local-first state object: structured profile + global notes + session notes | *Inject at session start* into the system prompt (YAML + markdown) with explicit precedence rules; the cookbook explicitly argues retrieval is "brittle to phrasing, prone to missing overrides" | Distil notes during a run via a tool; consolidate at session end (dedupe, recency wins, forgetting) | No | Good for small, high-signal state (our rules + lane summary). Does not scale to thousands of items: they warn memory "accumulates redundant and outdated information" without pruning. |
| **LangGraph Store + LangMem** [D] | Namespaced key-value docs (`(user, "memories")`); semantic = profile (one doc, updated) or collection (many narrow docs); episodic; procedural | Read by key, metadata filter, or optional embedding search at node time | Hot path (during run) or background formation | Optional | "Collection" = our ledger; "profile" = our lane summary. Docs note profiles get error-prone as they grow; collections shift complexity to search and update. Backends: Postgres, Mongo, Redis, Upstash. |
| **Letta (MemGPT)** [D] | Core memory *blocks* (always in context, size-limited, agent-editable), archival memory (unbounded, semantic search), recall (message history); all persisted in a DB | Blocks attached to the agent are pinned in the system prompt; archival only via tool call (`archival_memory_search`) | Agent edits blocks via tools; archival insert with tags | Archival: yes | Blocks = rules + lane summary; archival = ledger. Letta's own benchmark reports a plain filesystem with grep/search tools scored 74.0% on LoCoMo vs 68.5% reported for Mem0's graph variant, concluding "agent capabilities matter more than the tools" (vendor-reported, different harnesses). |
| **Mem0** [D] | Natural-language memories in a vector store (+ optional graph), SQLite history | Top-k similarity per query (paper: ~7k tokens per conversation vs ~26k full context; p95 1.44 s vs 17.1 s, vendor-reported) | LLM extracts salient facts from summary + last 10 messages, retrieves top-s similar memories, LLM picks ADD / UPDATE / DELETE / NOOP | Required (default `text-embedding-3-small`, local Qdrant) | Its LLM write path solves "unstructured chat to facts", which we do not have. Its dedupe-by-similarity step is the same retrieve-then-judge pattern I recommend, but done with lexical retrieval. |
| **Zep / Graphiti** [D] | Episodes (raw), entities and edges with bi-temporal validity; facts are *invalidated, not deleted* | Hybrid search: cosine + BM25 + breadth-first graph traversal, then rerank (RRF or cross-encoder), then a constructor formats text | Entity resolution = embedding + full-text candidates, then an LLM confirms duplicates | Required; needs Neo4j / FalkorDB / Neptune (Kuzu deprecated) | Heavy for a done-list. Useful ideas: append-only events, invalidate-not-delete, candidate generation then LLM adjudication. |
| **LlamaIndex Memory** [D] | FIFO short-term queue (default 30k tokens) + long-term blocks: Static, FactExtraction, Vector | Blocks have priorities; priority 0 is never truncated, higher numbers truncate first | Flushed messages feed the blocks | Only Vector block | Priority-ordered truncation is the right idea for our prompt: rules priority 0, brief truncatable. |

### 2.2 Cross-cutting findings

- **Two camps on retrieval.** Embedding-centred: Mem0, Letta archival, Zep, LangMem semantic search. No-embedding: Anthropic's file-based memory tool, OpenAI's inject-state pattern, Letta core blocks, LlamaIndex Static/Fact blocks. Benchmarks do not settle it (LoCoMo judges were audited as lenient, accepting up to 63% of intentionally wrong answers, reported by Mem0's own benchmark guide). [D, secondary]
- **What the frameworks do not do.** None of them target "negative memory" (do not repeat). They optimise recall of facts for personalisation. For "have I seen this item" the production analogue is a seen-set check in code (crawler URL-seen test, recommender "already seen" filters, often Bloom filters at web scale). [D for the pattern, I for the mapping]
- **Structured inputs need no LLM in the write path.** Mem0/LangMem spend LLM calls to turn conversation into memory objects. Your scout already returns structured fields (`series_issue_year`, `character`, `subject`, `sample_answer_items`), so writes can be deterministic. [I]
- **Always-on small block + on-demand big store** is the common shape (Letta core vs archival, LlamaIndex priority 0 vs vector, OpenAI injected state vs retrieval, Anthropic index file vs detail files). That maps to: rules + lane summary always in the prompt; the ledger queried per task. [I]
- **Summarising a list into prose is risky.** OpenAI's session-memory cookbook lists "context poisoning" and "summarization loss: subtle constraints may vanish" for summaries. That is what the LLM planner did to your exclusion list. [D, I]
- **Immutable history.** Graphiti invalidates instead of deleting; OpenAI consolidates with "most recent wins". For a ledger: status changes are new events; "unban" is an event. [D, I]

---

## 3. Q2 - Exclusion ("negative") memory

### 3.1 Evidence on long exclusion lists inside the prompt

All of this is indirect: **I found no study that tests exactly "90-line exclusion list inside a web-research-agent prompt".** [?]

- *Position and length.* Liu et al. (TACL 2024): accuracy is lowest when relevant information sits in the middle of a long context, higher at the start or end; going from 20 to 50 documents helped only ~1.5%. [D] Anthropic's own guidance says "as the number of tokens in the context window increases, the model's ability to accurately recall information from that context decreases." [D]
- *Context rot* (Chroma, 18 models incl. Claude Opus 4, GPT-4.1, Gemini 2.5): every model degrades with input length even on simple tasks; even one distractor hurts, four compound it; lower question-to-needle similarity degrades faster. [D, vendor tech report]
- *No literal match.* NoLiMa (ICML 2025): at 32K tokens 11 of 13 models fell below 50% of their short-context baseline; GPT-4o 99.3% to 69.7%. Relevant because a re-skinned item ("Venom #13" vs "Venom (2018) #13", paraphrased moment) has little literal overlap with its list line. [D]
- *Length alone hurts.* Even with perfect retrieval and masked/whitespace distractors, performance dropped 13.9% to 85% as input grew. [D]
- *Many constraints.* Success rate over n instructions decays roughly as p^n (ManyIFEval; a secondary summary reports GPT-4o prompt-level accuracy 0.94 with 1 instruction vs 0.21 with 10). Treat each list line as an implicit constraint. [D for the trend, secondary for the numbers]
- *Negation and priming.* "Semantic Gravity Wells" (arXiv 2601.08070): in a mechanistic study of **one 7B model (Qwen2.5-7B-Instruct)**, 87.5% of "do not use word X" violations were *priming* failures, where naming the forbidden token activates it. The authors recommend category-level constraints ("do not mention any cities"), positive reframing, and post-generation filtering. Anthropic's prompting guide says "Tell Claude what to do instead of what not to do." You.com's underlying model is unknown, so this transfers only as a weak prior. [D, caveat: single small model, preprint]
- *Your own data.* You.com returned items that were on the list (2026-08-05), and one issue was returned in 5 of 5 runs. [L]
- *Idea-generation analogy.* Si et al. appended all previously generated idea titles to the prompt and still ended with only 200 non-duplicate ideas out of 4,000 seeds, deduplicating afterward with sentence embeddings at cosine 0.8; the share of new unique ideas in each batch kept shrinking. [D] That is the same failure at 20x our scale, and the cure they used was a post-filter.

### 3.2 What production-style systems do instead [D unless marked]

- **Self-Instruct:** a generated instruction enters the pool only if ROUGE-L similarity with *every* existing instruction is below 0.7; the generator prompt shows only 8 sampled examples (6 human, 2 model-generated), not the whole pool.
- **The AI Scientist:** generates ideas, then filters by querying the Semantic Scholar API and discarding ideas too similar to existing literature (retrieval of relevant neighbours, not a full list in the prompt). The authors admit LLM novelty self-assessment is biased toward over-estimating novelty.
- **Zep entity resolution:** candidate duplicates come from embedding + full-text search, then an LLM adjudicates among those few candidates.
- **Crawlers and recommenders:** seen-set membership checked in code, Bloom filters when sets reach billions. At your scale a Python `set` does 100k lookups in 3 ms (my measurement).

### 3.3 Verdict [I]

| Approach | Effectiveness | Cost | Use here |
|---|---|---|---|
| Full done-list in prompt | Weak and degrading with size; truncation hides rules | Linear in ledger size, hits the 40k cap | No |
| Small *relevant* brief (top-K, category phrasing) | Modest help; steers the agent away from known-bad lanes and saves wasted calls | Constant | Yes, as an optimisation, never as the guarantee |
| Code-side post-filter on canonical keys | Deterministic, independent of model attention | Near zero | Yes, authoritative |
| Retrieve-then-judge with an LLM for the ambiguous band | Good for paraphrases | One cheap gate call, only for ambiguous candidates | Yes |
| Over-generate then filter, one retry with the specific collisions | Raises yield | Pennies at You.com prices | Yes |

Two more rules from the evidence: (a) rules go first and are never truncated (position effects; your 2010+ rule was being cut); (b) the exclusion text must be assembled by code after any LLM planner, never passed through it (TODO item 4).

---

## 4. Q3 - Storage layer

### 4.1 Options

| Option | Pros | Cons for you |
|---|---|---|
| **JSONL in git** | Human-readable, diffable, trivially backed up by git history, tolerant of a bad line, survives `projects/` cleaning, no binary merge. | Updates are new events (fine); same-file concurrent appends from two machines conflict; no built-in locking. |
| **SQLite** [D] | Atomic transactions ("either happen completely or not at all"), UNIQUE constraints, FTS5 with a `trigram` tokenizer (substring matching, SQLite >= 3.34), file format stable since 2004 and recommended for archival by the US Library of Congress, bundled in Python. WAL gives concurrent readers with one writer. | Same host only for WAL, avoid network filesystems and cloud-sync folders; copying a live DB mid-transaction yields a corrupt copy (use `VACUUM INTO`, backup API, or copy when idle, and keep `-wal` with the DB); binary file in git has no meaningful merge (you can add a `textconv` `.dump` diff driver for reading only). |
| **DuckDB** [D] | Fast analytics. | One read-write process; multi-process writes not supported in the native format (beta remote protocol or DuckLake+Postgres). Wrong shape for point lookups and tiny writes. |
| **KV stores** (dbm/shelve/LMDB) | Simple. | Opaque binary, platform quirks [?], no query for humans. No advantage over SQLite. |
| **Postgres** | Real multi-writer. | Operational burden you just removed elsewhere; SQLite docs themselves point to client/server DBs only when data and app are separated by a network. |

My micro-benchmark on the Mac (Python 3.13, SQLite 3.51, FTS5 trigram works): 20k rows insert in 0.03 s; 100 trigram `MATCH` queries in 0.02 s; **brute-force token-Jaccard over 20k rows costs 4.5 ms per query**; 100k set lookups in 3 ms. [L, measured] Conclusion [I]: up to ~10^5 entries you do not need an index at all; load the ledger into dicts at startup. Query FTS5 with quoted phrases (a bare `spider-m` raised `no such column: m`, because `-` is query syntax). On the Windows server verify `sqlite3.sqlite_version >= 3.34` and FTS5 before relying on trigram; fall back to brute force. [?]

### 4.2 Recommendation [I]

**System of record: append-only JSONL, git-tracked, one shard per writer machine** (`data/ledger/events.win-server.jsonl`, `events.mac-dev.jsonl`). Readers union all shards, de-duplicate by event id, sort by timestamp. Reasons: (1) git is already your sync path and the Windows/Mac topology means two writers; (2) disjoint files cannot conflict, and each line carries a unique id so a plain `merge=union` would also be safe for the same-file case; (3) the failure that hurt you was losing derived state, and git history is the cheapest durable backup.
Caveat on `merge=union` [D]: it takes lines from both sides in unspecified order and is unsafe if anyone edits a line in place or two writers can emit byte-identical records; hence append-only, one event per line, unique id.
In-process: a `Ledger` class folds events into `state[key]`. Optional derived `ledger.db` (gitignored, rebuilt when the JSONL changes) only if you want FTS5 or ad-hoc SQL.
Same-machine concurrency (UI process + CLI + scout runs): a single `append_event()` function with a file lock (for example `portalocker`, or `msvcrt`/`fcntl`), write one line, flush, fsync. Do not assume atomic O_APPEND on Windows. [?]
Conditional alternative: if only the Windows server ever writes and the Mac is read-only, SQLite canonical (WAL, `VACUUM INTO` nightly, JSONL export committed to git) is equally good and gives you locking and UNIQUE constraints for free. Never put the live `.db` in Google Drive/Dropbox (your `.gitignore` says outputs live in Google Drive). [D for SQLite, I for the conditional]

### 4.3 Ledger event schema (v1) [I]

```json
{"v":1,"id":"01J9...","ts":"2026-10-02T10:11:12Z","machine":"win-server","actor":"master|scout|gate|pipeline",
 "mode":"micro|qa|recap",
 "kind":"proposed|in_progress|produced|published|rejected|banned|unbanned|note",
 "stage":"scout|gate|narration|render|post",
 "unit":"issue|question",
 "key":"amazing-spider-man|2018|1",            // canonical issue key (null for pure question events)
 "keys":["detective-comics|2016|934", "..."],   // Q&A: answer item keys
 "label":"Amazing Spider-Man (2018) #1",
 "text":"When has Batman's no-kill rule failed...?", // question or moment text, for lexical match
 "entities":["batman"],                          // character / subject tags (from structured fields)
 "scope":"item|series_lane|character_lane",
 "reason_code":"single_issue|pre_2010|youtube_saturated|master_rejected|too_niche|dup_of",
 "reason":"free text, any language",
 "refs":{"project":"batcave-breach","session":"...","prompt_hash":"...","batcave":"6495/33723","fandom":"Amazing_Spider-Man_Vol_5_1","urls":[]},
 "expires":null}
```

Current state per key = fold of events with precedence `banned > produced/published > in_progress > rejected > proposed`, and `unbanned` clears a ban. `proposed` can carry a TTL so surfaced-but-undecided items reappear after N days; `banned`/`produced` never expire. `scope` separates item-level facts ("Master rejected this issue") from lane-level ones ("Absolute Batman lane saturated"), which is where false positives come from (section 6.5). Write it once as data and keep unrecognised fields on read (forward compatibility via `v`).

---

## 5. Q4 - Canonical identity without embeddings

### 5.1 Why identity is hard here [D unless marked]

- Amazing Spider-Man (2018 series) #1 is also legacy issue #802; legacy numbers are used "sporadically". The same run is "Amazing Spider-Man Vol 5 (2018-2022)" on Marvel Fandom, "The Amazing Spider-Man (2018 - 2022)" on marvel.com, and a separate Comic Vine volume (ids prefixed `4050-`), with a different volume entry for the Nick Spencer run. The Marvel Fandom page title for the issue is `Amazing Spider-Man Vol 5 1`. Dec cover dates carry the next year, so year +/-1 drift exists. [I for the drift]
- ComicInfo.xml has `Series`, `Number`, `Volume`, `Year`, `Count`, but "no explicit unique identifier mechanism"; `Volume` and `Year` are different concepts. So any "Series + Volume + Number" string is inherently ambiguous without IDs.
- "Annual", "Special", and 'The' prefixes live inside the series name in some sources and outside in others (your own `_same_issue` comment documents the Batman Annual case).

### 5.2 External IDs

| Source | What it gives | Constraints |
|---|---|---|
| **batcave reader ids** `https://batcave.biz/reader/{series}/{issue}` [L] | Stable numeric ids for everything that reached Stage 2 | Only exists after fetch; best `key` alias for produced items |
| **Fandom slug** (marvel/dc fandom are already in `include_domains`) [L] | `Amazing_Spider-Man_Vol_5_1` parses to series + vol + number; free from evidence URLs | Vol-to-year mapping needs the series page; wiki-specific |
| **GCD (comics.org)** [D] | Series id and issue id per row; ~212k series / 2.1M issues (Mar 2025); no API, bi-weekly MySQL dump (needs registration); a third-party SQLite extract (~230k series, 2.2M issues, 33.7 MB zip) exists | CC BY-SA 4.0; check the share-alike implications of a derived alias table [?] |
| **Comic Vine** [D] | Volume and issue ids, search endpoint, `4050-` volume ids | API "strictly for non-commercial use"; 200 requests per resource per hour; likely a problem for a monetized YouTube channel [?] |
| **Metron / Mokkari** [D] | REST API, issues/series | 20 req/min, 5,000/day; licence not checked [?] |

Recommendation [I]: do not make any external lookup a dependency of the core. Store whatever ids you already have (`batcave`, `fandom`) in `refs`, and consider an *offline* GCD-derived alias table later as pure data enrichment (per your CLAUDE.md, data-level enrichment is fine).

### 5.3 Matching ladder [I]

1. **L0 external id** equality (batcave ids, fandom slug).
2. **L1 exact canonical key**: `series_slug|series_start_year|issue_number`.
   Normalisation: NFKD to ASCII, casefold, drop a leading "the", `&` to `and`, punctuation to spaces, keep "annual/special" in the series slug, issue number parsed with your existing `_ISSUE_RE` (supports `1`, `1.5`, `1A`).
3. **L2 alias table** (data file `aliases.json`: "asm", "tasm", "amazing spiderman" to the slug; `vol 5` to 2018; legacy `802` to `#1`). It grows from the near-duplicate audit (section 6.5).
4. **L3 fuzzy, only with the issue number equal (the natural blocking key)**: series-token Jaccard >= 0.8 and year compatible (equal, within the series run, or +/-1) is an auto-match; 0.5 to 0.8 is **ambiguous**, so route to the gate with the two strings; below 0.5 is distinct. Your 0.6 `_same_issue` threshold sits inside my ambiguous band; I would promote it from "drop" to "adjudicate". Thresholds are my starting point and **must be calibrated on your ~250 real entries** (build a small labelled pair set from known aliases) [?].
5. **Never fuzzy-match issue numbers** (`#1` vs `#12`).

Library notes [D]: `rapidfuzz` (MIT, wheels for Windows/macOS/Linux, needs Python 3.11+ and the VC++ 2019 redistributable on Windows) is convenient, but `fuzz.token_set_ratio` "returns 100.0 if one string is a subset of the other", which would equate "Batman" with "Batman: Year One". Use Jaccard/Dice on token sets or `fuzz.ratio` on normalised strings. Stdlib is enough at this scale (my benchmark above). MinHash LSH (datasketch) is for sublinear search over large corpora and its own docs say brute force stays competitive for small ones; SimHash (Manku et al.: 64-bit, k=3 for 8 billion pages) targets long documents, not 5-token titles; Fellegi-Sunter/Splink is a heavy probabilistic framework with the right idea (blocking + weighted field agreement) but overkill. [D, I]

### 5.4 Q&A identity (no embeddings) [I]

A Q&A video is defined by its answer set. Use two cheap signals: (1) question text overlap (your current containment test, but against *Q&A-mode entries only*), and (2) **overlap of answer-item keys**: if two questions share >= 2 of 3 issue keys, or |A intersect B| / min(|A|,|B|) >= 0.5, treat them as the same video regardless of wording. `sample_answer_items` already comes back at discover time. This also resolves the *Final Crisis #6 in 5/5 runs* effect: a popular issue may legitimately appear in many questions, so for Q&A an issue-level hit is a **soft** signal (count it, cap reuse), while for micro and recap an issue-level hit is **hard**.

| Mode | Dedup unit | Hit means |
|---|---|---|
| recap | issue (or one-shot) key | hard exclusion |
| micro | issue key (policy choice: one micro per issue; scene-level dedup is fuzzy and I would avoid it) | hard exclusion, plus lane saturation signal |
| Q&A | question text + answer-key-set overlap | hard when overlap is high; per-issue reuse capped |
| cross-mode | recap of issue X makes a micro from X a likely repeat | policy decision for Master [?] |

---

## 6. Q5 - Recommended architecture

### 6.1 Overview [I]

```
        WRITE PATH (deterministic, no LLM)                 READ PATH
  UI buttons / CLI / project_factory / gate                 scout run (mode, intent, angle)
        |  append_event()  (lock, fsync)                          |
        v                                                         v
  data/ledger/events.<machine>.jsonl  (git)  --fold-->  Ledger (dicts; optional ledger.db FTS5)
        ^                                                         |
        | one-way import (idempotent)                             +--> PROMPT BLOCK (code-assembled, AFTER the planner)
  projects/*, banlist.md, candidates.csv, bank.md                 |      1. RULES  (curated, <=1.5k, priority 0, never cut)
                                                                  |      2. LANE SUMMARY (auto, <=1k)
                                                                  |      3. AVOID BRIEF (top-K relevant, <=2.5k, truncation reported)
                                                                  |
                                                                  +--> You.com call(s) -> candidates
                                                                  |
                                                                  +--> POST-FILTER (authoritative): L0..L3 ladder
                                                                         hit -> drop + reason; ambiguous -> gate(top-3 neighbours)
                                                                         then Q&A answer-set check, year/format rules
```

### 6.2 Three kinds of memory with three read paths [I]

Mapped to the LangMem taxonomy [D]: rules = *procedural*, lane summary = semantic *profile*, ledger = semantic *collection*, `research_sessions/` audit = *episodic*.

- **Rules** (HARD RULES, 2010+, 3+ comics, "who DID not who CAN"): hand-curated text, first in the prompt, asserted by a unit test, never truncated. The `.claude/memory/scout_*.md` lessons are the source; curate them into this block rather than appending them raw.
- **Lane summary**: auto-derived counts per series and character for the current mode, e.g. "Done in Batman/Q&A: 14 questions; most used issues: ...; saturated series: Absolute Batman (8)". Category-level phrasing follows the priming-avoidance recommendation. [I; D for the recommendation, weak evidence]
- **Avoid brief**: top-K ledger entries retrieved by lexical overlap with the plan's entities and angle (series/character tokens, BM25 or trigram), ranked by status weight and recency, one short canonical label per line (`Series #N (year)`), K about 15 to 25 and a hard char budget.

### 6.3 Write path: when entries are recorded [I]

| Moment | Event | Why |
|---|---|---|
| Candidate surfaced to Master | `proposed` (+ TTL) | stops the same candidate being re-presented every run |
| Evidence gate verdict `rejected` | `rejected` with `reason_code` | saves paying for the same gate call again |
| Master rejects / bans | `rejected` or `banned` (+ `scope`) | replaces ban-list rows; markdown becomes a rendered view |
| Project created from a candidate | `in_progress` + project slug | your rule "produce, then ban immediately" (2026-07-10) |
| Narration/audio/render/post milestones | `produced` / `published` / `rejected(stage=...)` | one project can end as "produced to audio, banned before render" (ban-list 2026-07-17) |
| Reconcile job | idempotent upsert from `projects/`, banlist, CSV | importer only, never the reverse; cleaning `projects/` no longer loses memory |

Event ids are deterministic (hash of kind + key + session) so retries and imports cannot double-write. Filter drops are not recorded as events (noise); count them in the audit log.

### 6.4 Read path: what goes into the You.com prompt, what is filtered in code [I]

- **Prompt (target 4 to 5k chars, was ~11k):** invariant rules, task and angle, then the lane summary and avoid brief. Today 9,000 of ~11,000 chars is the digest [L]; the new brief is <= 2.5k chars and does **not** grow with the ledger. Reuse your existing deterministic `assemble_prompt` order, but inject the brief there, not through the LLM planner.
- **Structured output:** return `series`, `volume_start_year`, `issue_number`, `legacy_number`, `character` as separate fields instead of one `series_issue_year` string, so the key is built without regex guessing. The schema limits (depth 5, 100 properties, 25,000-char schema string budget, every property required, `additionalProperties:false`) leave ample room. [D]
- **Post-filter (authoritative)** over every candidate: key ladder against the ledger; Q&A answer-set overlap; existing year/format rules. Output a "dropped as burned" report with the colliding ledger entry (you already print this).
- **Over-generate and retry once:** ask for 2 to 3 times the wanted count; if > 50% are dropped, run one more call whose brief lists *only the specific colliding keys* and says to pick different lanes. Cost: standard about $0.05, deep about $0.10 per call [D]. Prefer a fresh angle over a longer list.
- **Gate (OpenRouter):** for survivors in the ambiguous band only, give the gate the candidate plus its top-3 ledger neighbours and ask one question: "same issue/scene as any of these? which?" This is the Zep/AI-Scientist retrieve-then-judge pattern with lexical retrieval. [I]
- **Positive steering for recap:** your `comic_candidates.csv` already has `queued` rows; working the backlog first costs zero API calls (the existing `bank_fallback` Tier A is the same idea).

### 6.5 Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| Alias miss lets a duplicate through (e.g. "ASM Vol 5 #1") | alias table; ambiguous band to gate; **near-duplicate audit**: periodically list ledger pairs with Jaccard >= 0.7 not linked, review, add aliases |
| False positive blocks a good candidate (e.g. a whole series lane banned for 3 of 7 hits) | `scope` + expiry on lane-level blocks; the "dropped as burned" report shows the cause; one-flag override for Master |
| Ledger lost or corrupted | git history; append-only; reader skips bad lines and reports them; nightly `git` commit of the shard |
| Two machines diverge | per-machine shards; union on read; unique event ids |
| Concurrent writers on one machine | single `append_event()` with a lock |
| You.com ignores or misreads the brief | by design the brief is advisory; the post-filter is the guarantee |
| Filter drops most results, wasting calls | over-generate, one retry with collisions, fresh angle |
| Semantic re-skin of a Q&A question | answer-set overlap + gate on top-3 neighbours |
| Brief truncated silently | budget accounting that logs and asserts; rules in a separate never-truncated block |
| Schema drift or a bad status semantics | `v` field, tolerant reader, `reason_code` vocabulary |
| Too-small K misses relevant exclusions | replay test (below) measures repeat rate before/after |

**Evaluation:** replay the 10 scout runs preserved from 2026-10-02 and past ban-list cases against the new pipeline; track repeat rate (surfaced candidates colliding with the ledger), drop rate, cost per kept candidate. [I]

### 6.6 On embeddings [I]

Not "clearly best practice" for this problem: the exact-identity part is solved by keys, and the leading memory docs from Anthropic and OpenAI do not use embeddings for state of this size. The one place embeddings add recall is paraphrased Q&A questions. Decide with data: after phase 3, log how many repeated questions the gate/Master catch that the lexical + answer-set checks missed. If material, a **local in-process** option has no API, no hang risk: `model2vec` static embeddings (MIT, ~30 MB for potion-base-8M, base package depends only on numpy, "small drop in performance" vs full models) or `fastembed` (Apache-2.0, ONNX Runtime, no PyTorch/GPU, default `BAAI/bge-small-en-v1.5`), or `all-MiniLM-L6-v2` (22.7M params, 384-dim, Apache-2.0, needs PyTorch which you already carry for Magi). Precompute at write time, store the vector with the event, brute-force cosine in numpy over a few thousand rows (well under a millisecond, my estimate). Cost is a one-time model download plus a threshold to calibrate. The operational risk you hit before (remote service, 31-minute hang) disappears; new risks are a model file to package on Windows and a threshold to tune. [D for model facts, I for the rest]

---

## 7. Phased migration from today's digest [I]

| Phase | Work | Size | Exit test |
|---|---|---|---|
| **0** (no ledger) | Move HARD RULES to the fixed prompt head and assert it; split the digest by mode (Q&A prompt without recap titles); never truncate silently (log + test); move the exclusion list to code-assembled text after the planner | hours | Rules present in every rendered prompt; prompt <= 6k chars |
| **1** | `ledger.py`: event model, `append_event`, fold, `lookup(candidate)`; one-time idempotent import of banlist, CSV, bank, `projects/*/answer_context.json`, and any server `projects/`; **shadow mode**: compute ledger decisions next to old `is_burned`/`_same_issue`, log disagreements, change nothing | 1 to 2 days | Every legacy entry has a key; disagreement report reviewed |
| **2** | Switch the read path: post-filter from the ledger; avoid brief replaces the digest; wire writes (proposed, in_progress, rejected, produced) from UI, project factory and gate; ban-list markdown becomes a generated view | 2 to 3 days | Replay of the 10 runs shows fewer repeats; no rule truncation |
| **3** | Alias table, gate adjudication of the ambiguous band, answer-set check for Q&A, structured key fields in `output_schema`, near-duplicate audit, per-machine shards and git hygiene, over-generate-and-retry | 3 to 5 days | Calibrated thresholds on labelled pairs |
| **4** (only if needed) | SQLite FTS5 derived index (> ~20k events), offline GCD alias enrichment, local embeddings for Q&A paraphrases | optional | Triggered by measured misses, not by taste |

---

## 8. Trade-offs and what I am unsure about

- **Prompt-side effects are only weakly established for your setting** [?]: no study of exclusion lists in a web-research agent; the transformer-level negation result is a single 7B model; You.com's model and internal pipeline are unknown. The local evidence (items returned despite the list) is stronger than any paper I found.
- **Sync topology** [?]: I do not know which machines write the ledger or how the Windows server pushes to git. Per-machine shards are my hedge; a Windows server that cannot commit makes SQLite-on-server plus periodic export the better path.
- **Thresholds** [?]: 0.8/0.5 Jaccard, answer-set 0.5, K, TTLs are starting values, not findings.
- **Licensing** [?]: Comic Vine's API terms say non-commercial; GCD is CC BY-SA 4.0; I am not a lawyer and have not checked Metron's terms.
- **Policy questions for Master:** is one micro per issue the rule; does a recap of X block a micro from X; do `proposed` items expire.
- **Cost of the append-only design:** current state needs a fold on load (trivial at this size) and status semantics must be disciplined; a mutable table is simpler to read but loses history and git-diffability.
- **I did not verify** Windows `O_APPEND` atomicity, the Windows Python's bundled SQLite version, or the FTS5 build there.

---

## 9. Sources

Agent/LLM memory and context engineering
- Anthropic, Effective context engineering for AI agents: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, Memory tool docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Anthropic, Managing context (context editing + memory, 39%/29%/84%): https://claude.com/blog/context-management
- Anthropic, Effective harnesses for long-running agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic, Prompting best practices (positive instructions, long-context placement): https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- OpenAI cookbook, long-term memory notes (state injection): https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization
- OpenAI cookbook, session memory (trimming vs summarisation): https://developers.openai.com/cookbook/examples/agents_sdk/session_memory
- LangGraph stores: https://docs.langchain.com/oss/python/langgraph/stores ; LangGraph memory concepts: https://docs.langchain.com/oss/python/concepts/memory ; LangMem conceptual guide: https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Letta memory: https://docs.letta.com/guides/agents/memory ; archival: https://docs.letta.com/guides/agents/archival-memory ; filesystem benchmark: https://www.letta.com/blog/benchmarking-ai-agent-memory/
- Mem0 paper: https://arxiv.org/abs/2504.19413 (HTML https://arxiv.org/html/2504.19413); Mem0 OSS components: https://docs.mem0.ai/open-source/overview ; Mem0 benchmark guide (judge audit, secondary): https://mem0.ai/blog/ai-memory-benchmarks-in-2026
- Zep paper: https://arxiv.org/abs/2501.13956 (HTML https://arxiv.org/html/2501.13956v1); Graphiti: https://github.com/getzep/graphiti
- LlamaIndex memory: https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/

Long-context, negation and exclusion evidence
- Liu et al., Lost in the Middle (TACL 2024): https://aclanthology.org/2024.tacl-1.9/
- Chroma, Context Rot: https://www.trychroma.com/research/context-rot
- NoLiMa (ICML 2025): https://arxiv.org/abs/2502.05167
- Context length alone hurts performance: https://arxiv.org/abs/2510.05381
- When Instructions Multiply (ManyIFEval, EMNLP Findings 2025): https://aclanthology.org/2025.findings-emnlp.896/ ; Curse of Instructions summary (secondary numbers): https://maxpool.dev/research-papers/curse_of_instructions_report.html
- Semantic Gravity Wells (negative constraints, single 7B model): https://arxiv.org/abs/2601.08070 ; HTML https://arxiv.org/html/2601.08070
- Si et al., Can LLMs Generate Novel Research Ideas: https://arxiv.org/html/2409.04109
- Self-Instruct: https://arxiv.org/html/2212.10560
- The AI Scientist: https://arxiv.org/html/2408.06292
- Bloom filters for seen-item filtering (overview): https://www.freecodecamp.org/news/bloom-filters-explained/

You.com
- Research API reference: https://you.com/docs/api-reference/research/v1-research ; guide: https://you.com/docs/guides/research

Storage
- SQLite FTS5 (trigram, external content, bm25): https://www.sqlite.org/fts5.html
- SQLite WAL: https://www.sqlite.org/wal.html ; network filesystems: https://www.sqlite.org/useovernet.html ; how to corrupt (backups, live copies): https://www.sqlite.org/howtocorrupt.html ; VACUUM INTO: https://www.sqlite.org/lang_vacuum.html ; application file format: https://www.sqlite.org/appfileformat.html
- DuckDB concurrency: https://duckdb.org/docs/current/connect/concurrency.html
- Git `union` merge driver: https://git-scm.com/docs/gitattributes ; JSONL union-merge caveats: https://dev.to/rulestack/two-writers-one-append-only-ledger-the-git-conflict-one-gitattributes-line-fixed-and-the-files-55j0
- SQLite in git (textconv): https://dunkels.com/adam/git-diff-sqlite3/

Identity, fuzzy matching, entity resolution
- Comic Vine API terms: https://comicvine.gamespot.com/api/ ; docs: https://comicvine.gamespot.com/api/documentation
- GCD overview: https://en.wikipedia.org/wiki/Grand_Comics_Database ; third-party GCD SQLite extract (CC BY-SA 4.0, sizes): https://github.com/heisehis/paperbunkr-gcd-data ; GCD licence search result: https://www.comics.org/
- Metron/Mokkari (rate limits): https://mokkari.readthedocs.io/en/stable/index.html
- ComicInfo.xml schema: https://anansi-project.github.io/docs/comicinfo/schemas/v2.1
- Legacy vs volume numbering example: https://www.howtolovecomics.com/2023/09/13/marvel-legacy-numbering/ ; Marvel Fandom volume page naming: https://marvel.fandom.com/wiki/Amazing_Spider-Man_Vol_5
- RapidFuzz docs: https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html ; repo: https://github.com/rapidfuzz/RapidFuzz
- datasketch MinHash LSH: http://ekzhu.com/datasketch/lsh.html ; Manku et al. near-duplicates: https://research.google.com/pubs/archive/33026.pdf ; Splink / Fellegi-Sunter: https://www.robinlinacre.com/introducing_splink/ ; blocking survey: https://arxiv.org/pdf/1905.06167

Local embedding options
- model2vec: https://github.com/MinishLab/model2vec ; fastembed: https://github.com/qdrant/fastembed ; all-MiniLM-L6-v2: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

Local files read (read-only): `stages/youcom_scout.py`, `stages/research_scout/{planner,issue_identity,storage,bank_fallback,openrouter_gate}.py`, `qa_question_banlist.md`, `comic_candidates.csv`, `TODO_QA_SCOUT_2026-10-02.md`, `.gitignore`, `requirements.txt`.
