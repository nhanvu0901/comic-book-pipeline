# Documentation archive

Dated records of research, implementation, and verification. Statements about deployment, status, metrics, or next steps reflect the document date, not the current state of the pipeline. See the [documentation index](../README.md) for maintained entry points.

## Agent Handoffs

- [ANSWERER — dedicated Q&A session brief](agent-handoffs/answerer-brief.md)
- [Handoff: scout micro (You.com) và prompt Gemini writer — 2026-10-01](agent-handoffs/handoff-scout-and-gemini-prompt-2026-10-01.md)

## Architecture

- [How Claude Tool Use Works](architecture/claude-tool-use.md)
- [Stage 1 — PanelNarrator: Full Flow Documentation](architecture/stage_1_flow.md)

## Avatar Narrator

- [Nhân vật dẫn chuyện (avatar narrator) cho Shorts Grimframe — nghiên cứu 2026-11-05](avatar-narrator/2026-11-05-avatar-narrator-research.md)
- [Đề xuất: bước "Mascot" cuối pipeline — chọn vị trí, ghép face + body, chỉnh chuyển động — 2026-11-05](avatar-narrator/2026-11-05-mascot-step-design.md)

## Clip Video Stream Fix 2026 11 04.Md

- [Sửa clip không có video — 2026-11-04](clip-video-stream-fix-2026-11-04.md)

## Competitor Refresh 2026 11 03 Download.Md

- [Competitor refresh — 2026-11-03](competitor-refresh-2026-11-03-download.md)

## Competitor Writer Analysis 2026 11 03.Md

- [Đối chiếu narration đối thủ để cải thiện writer — 2026-11-03](competitor-writer-analysis-2026-11-03.md)

## Qa Scout

- [R3 - Văn liệu về cách nạp / điều kiện hoá context cho search agent (scout You.com)](qa-scout/2026-10-02-context-loading-papers.md)
- [R1 - Memory architecture for the comic scout ("never propose it again")](qa-scout/2026-10-02-done-memory-architecture.md)
- [R4 — Scout "novelty-first": để chính việc tìm kiếm đi vào vùng mới (không gửi danh sách đã làm cho You.com)](qa-scout/2026-10-02-novelty-search-papers.md)
- [R2 — You.com API, đa dạng hoá kết quả và dedup quanh bước search+LLM của scout](qa-scout/2026-10-02-youcom-dedup-and-exclusion.md)
- [A/B các chiến lược scout You.com — đo 2026-10-03 (Mac, không sửa code pipeline)](qa-scout/2026-10-03-scout-strategy-ab-test.md)
- [You.com Research API: prompt lớn có làm mất ngữ cảnh không? — đo 2026-10-03](qa-scout/2026-10-03-youcom-large-prompt-test.md)
- [Handoff: 3 việc còn dở của Q&A scout — 2026-10-04](qa-scout/HANDOFF_QA_SCOUT_REMAINING_3_TASKS_2026-10-04.md)
- [Handoff: luật cứng đặt đầu, bỏ digest, lọc trùng cho scout You.com — 2026-10-03](qa-scout/HANDOFF_SCOUT_HARD_RULES_AND_DEDUP_2026-10-03.md)

## Qa Writer Update Plan 2026 11 03.Md

- [Q&A writer contract implementation plan](qa-writer-update-plan-2026-11-03.md)

## Qa Writer Update Result 2026 11 03.Md

- [Kết quả cập nhật Q&A writer — 2026-11-03](qa-writer-update-result-2026-11-03.md)

## Research

- [Competitor & Algorithm Audit — 2026-07-12 (Phase 1, chưa nhìn code)](research/competitor-algo-audit-2026-07-12.md)
- [Phân tích toàn bộ corpus Shorts đối thủ — 2026-10-01](research/competitor-corpus-full-audit-2026-10-01.md)
- [Comic Shorts script audit — 2026-09-28](research/competitor-script-audit-2026-09-28.md)
- [Spoken opening audit — 2026-09-30](research/hook-opening-audit-2026-09-30.md)
- [Micro-moment scout: reference audit (2026-09-30)](research/micro-moment-reference-audit-2026-09-30.md)
- [Nhạc nền cho pipeline — research + số đo thật](research/music-scoring-research-2026-08-12.md)
- [Comic-Recap Shorts Production: Panel Selection & Editorial Heuristics](research/researcher-250220-comic-shorts-panel-selection.md)
- [2.000 → 10.000 views — Research tổng hợp & kế hoạch hành động](research/viral-2k-to-10k-plan.md)
- [Viral Upgrade Research v2 — Grimframe comic Shorts → 10k+ views](research/virality-upgrade-research.md)

## Superpowers

- [Stage 3 Narration Discipline — Implementation Plan](superpowers/plans/2026-06-06-stage3-narration-discipline.md)
- [Crossover-Saga Mode Implementation Plan](superpowers/plans/2026-06-13-crossover-saga-mode.md)
- [Art long-form — Chapter cards + anti-repetition Implementation Plan](superpowers/plans/2026-06-14-art-longform-chapter-separation-anti-repetition.md)
- [Channel-Match v2 — pipeline updates to copy @TheComicCivilian style](superpowers/specs/2026-05-23-channel-match-v2-design.md)
- [Pipeline v3 — narrative coherence + smart panel selection](superpowers/specs/2026-05-23-pipeline-v3-coherence-design.md)
- [Pipeline v4 — narrative voice + Claude Sonnet writer](superpowers/specs/2026-05-24-pipeline-v4-narrative-voice-design.md)
- [Pipeline v5 — VLM-enhanced Magi clusters + hybrid panel scoring](superpowers/specs/2026-05-24-pipeline-v5-magi-cluster-hybrid-design.md)
- [Stage 3 Narration Discipline — Design](superpowers/specs/2026-06-06-stage3-narration-discipline-design.md)
- [Art pipeline v3 — Long-form mode (8-12 min, 16:9, chaptered)](superpowers/specs/2026-06-12-art-longform-mode-design.md)
- [Unify the LLM backend across comic + art (single FREE_MODEL switch, SDK-only)](superpowers/specs/2026-06-12-unify-llm-backend-design.md)
- [Crossover-Saga mode — narrate a multi-issue storyline (≤5 issues) as one Short](superpowers/specs/2026-06-13-crossover-saga-mode-design.md)
- [Art pipeline long-form — Chapter separation + anti-repetition](superpowers/specs/2026-06-14-art-longform-chapter-separation-anti-repetition-design.md)
- [HF MiniMax Music 3 scoring for Stage 5](superpowers/specs/2026-08-19-hf-minimax-stage5-music-design.md)
- [Feed the evidence gate the sources the candidate actually cited](superpowers/specs/2026-09-19-evidence-gate-feed-it-the-cited-sources.md)
- [Re-scout while keeping what is already confirmed](superpowers/specs/2026-09-19-scout-rescout-keeping-confirmed-design.md)
- [Stage 1 Research Scout — single-selection flow redesign](superpowers/specs/2026-09-19-stage1-scout-flow-design.md)

## Tts Wait Fix 2026 11 04.Md

- [Clip pick waiting TTS — 2026-11-04](tts-wait-fix-2026-11-04.md)

## Video Clip Feature

- [Hình ảnh cho Shorts: panel comic, clip phim/hoạt hình, hay AI? — nghiên cứu 2026-10-03](video-clip-feature/2026-10-03-visuals-panels-vs-video-ai.md)
- [Lấy clip mp4 ngắn (2–4 s) của nhân vật để chèn vào shot quan trọng — nghiên cứu 2026-10-04](video-clip-feature/2026-10-04-character-clip-sources.md)
- [Shot video clip trong Stage 5: cách dùng (2026-10-06)](video-clip-feature/2026-10-06-clip-shots-usage.md)
- [Handoff: phân tích GiyeSears và khả năng dùng clip MP4 trong comic-book-pipeline](video-clip-feature/2026-10-06-giyesears-video-handoff.md)
- [IMPLEMENTATION PLAN: Tích Hợp Video Clips Vào Pipeline Q&A (Phân Chia Worker & Delivery Option A)](video-clip-feature/2026-10-07-video-qa-plan.md)
- [SPEC: Tích Hợp Video Clips Vào Pipeline Q&A (Comic Q&A Hybrid & Screen Q&A)](video-clip-feature/2026-10-07-video-qa-spec.md)
- [P1 verify — bàn giao (cbp-18, nhánh `video-qa/p1-verify`)](video-clip-feature/2026-10-08-p1-verify-handoff.md)
- [P3 core handoff — `video-qa/p3-core` (screen_qa research / narration / CLI)](video-clip-feature/2026-10-08-p3-core-handoff.md)
- [P3 visuals (`video-qa/p3-visual`) — what shipped, the contracts, the evidence](video-clip-feature/p3-visual-notes.md)

## Verification evidence

- [P1 screenshots and archived harnesses](video-clip-feature/p1-verify-evidence/)
- [P3 screenshots, reports, and archived harnesses](video-clip-feature/p3-visual-evidence/)

These harnesses are historical verification artifacts, not maintained pipeline entry points.
