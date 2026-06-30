# Scout: Read ban list BEFORE any search (2026-06-30)

## Rule
Before doing ANY discovery search or batcave verify in comic-scout runs, read the FULL ban list from the agent prompt:
1. "Already produced" list (persistent dedup)
2. "User-rejected" list
3. Any per-run HARD BAN the user states in their message

Only THEN start searching. If a candidate matches ANY entry in those lists → skip immediately, do NOT search/verify it.

## Why this matters
A run on 2026-06-30 searched for "Joker: Year of the Villain" and was about to search X-Men Annual / Iron Man Annual / Thor Annual #1 (already produced!) — wasting tool calls on banned/produced titles. The user interrupted and noted the agent is not reading the ban list.

## Quick reference — things that are ALWAYS banned (as of 2026-06-30)
- Any Batman-led or Batman-co-lead story (current hard ban)
- Thor Annual #1 (produced — MODOK/Yggdrasil)
- Doctor Strange: The End (produced)
- Deadpool/Batman crossover (produced)
- All titles in "Already produced" list in agent prompt
- All titles in "User-rejected" list in agent prompt
- All per-run bans stated by user in their message

## Pattern to avoid
"Let me search for X" → [search] → realize X is banned → wasted call.

## Correct pattern
1. Read full ban lists (produced + rejected + per-run)
2. Build internal exclusion set
3. ONLY search for things NOT in the exclusion set
