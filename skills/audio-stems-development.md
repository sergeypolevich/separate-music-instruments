# Audio Stems Development Workflow

Read this file and the linked project specification in `AGENTS.md` before each implementation stage. Keep work scoped to one stage at a time.

## Before editing

1. Read the current instructions, relevant code, dependencies, tests, and the latest entry in `DEVELOPMENT_STATUS.md`.
2. List the stage's corner cases, risks, and behavior boundaries before writing tests. Preserve the working baseline unless an explicit behavior change is requested.
3. Write a short implementation plan and a check matrix with expected results in the stage notes.
4. Start with tests, then make the smallest useful change. Keep external downloads, YouTube, and real Demucs execution out of default tests.

## Verification and review

- Run the stage-specific checks and the complete offline suite. Live YouTube/Demucs tests are opt-in only.
- **Before every commit, also run the full real-browser Short Spoken Clip E2E (`tests/test_e2e_short_clip.py`, Singer - Short Spoken Clip, a 16-second track)**, using the actual API and separate worker plus the real pytubefix downloader and CPU Demucs separator: `$env:RUN_LIVE_E2E = "1"; py -m pytest -q -m e2e -s`. Playwright drives the installed Edge or Chrome in a visible window; do not substitute headless browser media or fakes for this gate. It intentionally needs internet, an installed browser, disk space, model weights, and several minutes. If it fails or cannot run, do not commit; record the blocker and keep working or report that the commit gate is unmet. The normal offline suite remains network-free.
- Report actual command results. Do not move to the next stage or commit if a required check fails; fix it within the current stage.
- Review the diff for accidental behavior changes, unsafe paths, secrets, local media, generated stems, databases, temporary outputs, and log files.
- Update `DEVELOPMENT_STATUS.md` with actual changes and verification results before committing. Commit only after checks pass and working behavior is confirmed.

## Commit and push policy

- **Commit automatically after every completed step.** When a step's checks (offline suite, stage checks, and the live Short Spoken Clip E2E gate above) pass and the diff is reviewed, create a local commit without asking. One step = one focused commit with a descriptive message. Do not batch several steps into one commit, and do not commit a step whose gate failed or could not run — report the blocker instead.
- Before committing, verify that no local media, stems, databases, logs, temporary outputs, or secrets are staged (`git status`, `git diff --cached --stat`). Stage files explicitly rather than with blanket `git add -A` when untracked files are present.
- **Do not push during a version.** Pushing to `origin` happens only at the end of a version, after the user has manually verified it and explicitly asked to push. Until then, commits stay local.

## Logging contract

Use readable console logs and rotating JSON Lines logs. Emit UTC ISO-8601 timestamps and fields `timestamp`, `level`, `module`, `event`, exact-case `ClientID` (JSON null when absent), `batch_id`, `job_id`, and `message`. Include `attempt` for retries and `error_type` plus exception/stack details for failures. Default to INFO; default rotation is 10 MB with five backups. Make these settings configurable. Never serialize passwords, tokens, cookies, or secret configuration. Persist business job events in the database separately from text logs.

## Checkpoint and handoff

Before each stage, assemble a compact working packet from `AGENTS.md`, this workflow, that stage's plan, completed status notes, and only the relevant source/tests. Record enough context in `DEVELOPMENT_STATUS.md` to resume after conversation compaction. Do not claim a token count; if work must pause, record exact progress, checks, blockers, and next action.

## Stage report

Report the objective, files and behavior changed, commands and actual outcomes, expected results, limitations/blockers, commit ID (if one was created), and the next stage. Do not claim a stage is complete when required verification remains.
