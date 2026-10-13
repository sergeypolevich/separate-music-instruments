# Audio Stems Development Workflow

Read this file and the linked project specification in `AGENTS.md` before each implementation stage. Keep work scoped to one stage at a time.

## Before editing

1. Read the current instructions, relevant code, dependencies, tests, and the latest entry in `DEVELOPMENT_STATUS.md`.
2. List the stage's corner cases, risks, and behavior boundaries before writing tests. Preserve the working baseline unless an explicit behavior change is requested.
3. Write a short implementation plan and a check matrix with expected results in the stage notes.
4. Start with tests, then make the smallest useful change. Keep external downloads, YouTube, and real Demucs execution out of default tests.

## Verification and review

- Run the stage-specific checks and the complete offline suite. Live YouTube/Demucs tests are opt-in only.
- Report actual command results. Do not move to the next stage or commit if a required check fails; fix it within the current stage.
- Review the diff for accidental behavior changes, unsafe paths, secrets, local media, generated stems, databases, temporary outputs, and log files.
- Update `DEVELOPMENT_STATUS.md` with actual changes and verification results before committing. Commit only after checks pass and working behavior is confirmed. Never push or publish without explicit user authorization and a configured destination.

## Logging contract

Use readable console logs and rotating JSON Lines logs. Emit UTC ISO-8601 timestamps and fields `timestamp`, `level`, `module`, `event`, exact-case `ClientID` (JSON null when absent), `batch_id`, `job_id`, and `message`. Include `attempt` for retries and `error_type` plus exception/stack details for failures. Default to INFO; default rotation is 10 MB with five backups. Make these settings configurable. Never serialize passwords, tokens, cookies, or secret configuration. Persist business job events in the database separately from text logs.

## Checkpoint and handoff

Before each stage, assemble a compact working packet from `AGENTS.md`, this workflow, that stage's plan, completed status notes, and only the relevant source/tests. Record enough context in `DEVELOPMENT_STATUS.md` to resume after conversation compaction. Do not claim a token count; if work must pause, record exact progress, checks, blockers, and next action.

## Stage report

Report the objective, files and behavior changed, commands and actual outcomes, expected results, limitations/blockers, commit ID (if one was created), and the next stage. Do not claim a stage is complete when required verification remains.
