---
name: book-to-podcast
description: Turn a long text, OCR'd book, or PDF into broadcast-ready Chinese podcast scripts (per-chapter .md plus clean .tts.txt) and generate TTS audio. Use when adapting a book / PDF / any long text into a podcast, audiobook, or spoken-word audio.
agent_created: true
---

# Book → Podcast Pipeline

Turn a long text (typically an OCR'd book markdown or a PDF) into a spoken-word podcast in two phases. Both phases are deterministic and script-assisted; the judgment work is in Phase 1 (writing faithful, broadcast-quality scripts). **The skill is book-agnostic** — book-specific data lives in `references/briefs/<slug>.yaml` (see Phase 1), not hardcoded here.

## When to use
- "把这本书做成播客 / 有声书 / 讲稿"
- "扫描的 PDF / OCR 稿转成口播稿"
- "生成 TTS 语音"
- Any task combining faithful text adaptation with audio synthesis.

## ⛔ Directory & path rules (READ FIRST — hard constraints)
**The skill directory `skills/b2p/` is READ-ONLY shared code. Never write project artifacts into it.**
- All outputs (`.md`, `.tts.txt`, `.mp3`/`.wav`, logs, temp scripts) go ONLY in the **caller's project directory** — never in `skills/b2p/`, its `scripts/`, or `scripts/audio/`.
- Run every `scripts/xxx.py` from the **project directory**; scripts resolve their own code/config (apis.yaml, `tts_engine/`, `anchor/`) read-only from `skills/b2p/`, and resolve inputs/outputs from the current working directory.
- `tts_run.py` hard-codes `AUDIO_DIR = HERE/audio` and globs `HERE/*.tts.txt` — do NOT work around this by copying artifacts into `scripts/`. Use `--out-dir` (or a throwaway project subdir, cleaned up after) so nothing lands in the skill dir. See `references/CONVENTIONS.md` §0–§3.
- **Finish check is bidirectional**: verify deliverables exist in the project dir **AND** that `skills/b2p/` has zero new files from this run. Full rules: **`references/CONVENTIONS.md`** (single source of truth for dirs/paths/artifacts/logs).

## Environment (read first)
- **Python**: the scripts assume a Python 3.10+ on your `PATH`. Run them as `python scripts/xxx.py …`. If your interpreter isn't the default `python`, substitute your own (e.g. `python3`, or an absolute path) — nothing here hardcodes a specific interpreter.
- **Dependencies**: `pip install -r requirements.txt` (see that file). Core: `pyyaml`, `edge-tts`, `httpx[socks]`, `aiohttp_socks`, `imageio-ffmpeg`.
- **Network / proxy**: Fish Audio and 小米 mimo need egress; if you're behind a firewall/SOCKS5 proxy, configure it in `scripts/apis.yaml` (`socks:` field) and ensure the runtime can reach the network.

> ### ⚠ WorkBuddy-only notes (skip if you're not in WorkBuddy)
> These pitfalls apply **only** to WorkBuddy's sandboxed Bash, not to a normal terminal:
> - **`os.remove` / `os.unlink` are wrapped** in a "safe-delete" shim that moves to an (unavailable) recycle bin → any delete raises `OSError`. **Never call `os.remove`**; overwrite files with `'wb'` instead. (All skill scripts already follow this.)
> - **Network needs `dangerouslyDisableSandbox: true`** on the Bash tool to reach external APIs / the local SOCKS5 proxy.
> - **Background-task path quirk**: in `run_in_background` Bash, an absolute `/c/Users/.../script.py` arg can be mis-joined; `cd` into the dir first, then use the relative name `script.py`.

## Phase 1 — Scripts (the human-judgment part)
Goal: one `.md` per chapter, broadcast style, faithful to source, plus a clean `.tts.txt`.

1. **If the source is a raw PDF (text-layer or scanned), do PDF ingestion + image understanding first** — see `references/workflow.md` 阶段〇 (render scanned pages → visual page-read → extract chart content into oral descriptions). Skip this only if you already have clean OCR markdown.
2. **Generate / load this book's brief** (book-specific config, NOT hardcoded in the skill):
   - For a new book, create `references/briefs/<slug>.yaml` from the fields in `references/style_brief_template.md` ("每本书开始前要填的槽位"): `title`, `author`, `samples`, `white` (allow-list of foreign proper nouns for the `audit.py` gate), plus optional `glossary`/`notes`.
   - Read `references/style_brief_template.md` for the exact structure, quality rules, and length targets. Adapt per book but keep the rules.
3. **Sample-first**: write 2–3 sample chapters, get user sign-off on voice/structure before full generation.
4. Generate per-chapter `.md` (use sub-agents for bulk, but verify real output by char-count, not by receipt).
5. **Hard rule (user mandate)**: agent additions (exercises, examples) are *additive only* — never compress or simplify the source to make room. Label added blocks with `（主播延伸 · …）` and anchor each to a specific source argument. Short chapters stay honestly short; never pad.
6. Run `python scripts/make_tts.py` from the output dir to strip bracket-labels and emit `*.tts.txt`.
7. **Gate before delivery**: run `python scripts/audit.py <outdir> --book <slug>` → must report `noise=0` and `strayEN=0` (the `white` list from the brief is the only allowed foreign text). Re-run until clean. See `references/workflow.md` §1.4.1.
8. For over-long chapters, compress to ≤30 min *without losing any argument* — keep every distinct point, cut only redundancy; back up the full version first (see `references/workflow.md`).

## Phase 2 — TTS audio

Two code paths share the same `.tts.txt` inputs:

### 2a. `tts_engine` (RECOMMENDED, multi-provider)
Multi-API abstraction: Fish Audio S2.1 Pro, Microsoft Edge-TTS (free), or Xiaomi mimo (incl. voice-design & voice-clone). Usage (from `scripts/`):
```
python tts_run.py                                            # default profile, all *.tts.txt
python tts_run.py --api edge_tts                            # free Microsoft voice
python tts_run.py --api mimo-v2.5-tts-voiceclone            # voice-clone (pins speaker across chapters)
python tts_run.py --api mimo-v2.5-tts-voiceclone --anchor path/to/xxx.mp3
python tts_run.py --list                                    # list available profiles
```
Detail: `references/workflow.md` §2.7. **New TTS API = one `providers/<name>.py` subclass + one `apis.yaml` profile; orchestrator unchanged.**

### 2b. `fish_tts.py` (LEGACY — single-API quick path)
A standalone Fish Audio S2.1 Pro script (SOCKS5 + chunked decoding + resume). Kept as a **legacy convenience** for quick single-provider batches. **Do not run it against the same chapters as `tts_engine` simultaneously** — they write the same-named `.mp3` files and will clobber each other. Prefer `tts_engine` for anything multi-provider.

## Critical reminders
- **Never call `os.remove`/`os.unlink`** in WorkBuddy's sandbox (see WorkBuddy-only notes). Overwrite with `'wb'`.
- Fish Audio returns **chunked** HTTP; httpx auto-decodes it, so mp3s come out clean.
- **Don't run `fish_tts.py` and `tts_engine` on the same chapters at once** (write conflict).
- Keep book-specific data in `references/briefs/`, never inline in scripts or docs.
- **Never write project artifacts into the skill directory** (`skills/b2p/`, `scripts/`, `scripts/audio/`). Outputs belong in the caller's project dir only. Bidirectional finish check: deliverables present AND skill dir unpolluted. See `references/CONVENTIONS.md`.
