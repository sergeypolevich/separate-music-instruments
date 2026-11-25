# Analytics

What was pressed, on which screen, and what failed: one row per event in `~/Audio Stems/analytics.db` (SQLite),
laid out like GA4's BigQuery export, for SQL. It is kept forever and never leaves the computer. Open it with any
SQLite tool (`sqlite3 "%USERPROFILE%\Audio Stems\analytics.db"`, DB Browser for SQLite, DBeaver) while the app runs.

The JSON log (`logs/app.jsonl`) is for finding out why something broke: it is rotated (10 MB × 5) and has the stacks
and the server's steps. Clicks, keys and setting changes are only here; warnings and errors are in both (a short
message here, the full stack there).

## Table `events`

| Column | Meaning |
|---|---|
| `event_timestamp` | microseconds since 1970-01-01 UTC (`datetime(event_timestamp / 1e6, 'unixepoch')`) |
| `event_date` | `YYYYMMDD`, UTC |
| `event_name` | see below |
| `level` | `info`, `warning`, `error` |
| `source` | `page` (the browser) or `server` (API, worker, analysis) |
| `install_id` | random, made once per data folder (table `install`); not tied to a person or a computer |
| `session_id` | one page load (a browser tab); empty for the server |
| `screen` | `projects`, `project`, `settings`, `store`, `help`, `search`, `confirm`, … (the topmost open window) |
| `project_id` | the open project (page) or the project the event is about (server) |
| `plugin` | the plugin that logged the event |
| `app_version`, `platform` | `25`, `windows` / `macos` / `linux` |
| `device` | `desktop` or `phone` (a narrow or touch screen); page events only |
| `params` | JSON, read with `json_extract(params, '$.control')` |

Events and their `params`:

- `ui_click` — `control` (the button's `data-action` or id, else its label), `text` (the label as shown, in the
  page's language), `place` (the section or window), `pressed` for toggles.
- `ui_key` — `code` (`Space`, `KeyQ`, … by position, not by layout), `ctrl`, `shift`. Keys typed into a field are
  not logged.
- `ui_change` — `control`, `value` (a checkbox's state, a slider's or a list's value; for a text field only its
  length, never the text).
- `api_failed` (`method`, `path`, `status`, `code`, `message`, `ms`), `api_unreachable`, `ui_error` (`message`,
  `source`, `line`, `stack`), `plugin_failed`.
- `<plugin>_<event>` — a plugin's own steps (`ctx.log`), for example `practice_tuner_finished`.
- Server: `batch_created`, `worker_started`, `audio_download_reused`, `job_retry_scheduled`, `job_cancelled`, and
  every warning and error (`separation_failed`, `audio_download_failed`, …) with `message` (≤ 500 characters,
  secrets masked), `error_type`, `job_id`, `batch_id`.

Song titles, links, search queries and file names are not written as fields; a server error message may quote one
(for example «Video unavailable: …»).

The projects themselves (what was downloaded and separated, with which model and how long it took) are in the app's
own database `audio_stems.db`; the queries below attach it.

## Queries

```sql
-- 1. Events per day and source
SELECT event_date, source, count(*) FROM events GROUP BY 1, 2 ORDER BY 1 DESC;

-- 2. The buttons pressed most, by screen
SELECT screen, json_extract(params, '$.control') AS control, count(*) AS clicks
FROM events WHERE event_name = 'ui_click' GROUP BY 1, 2 ORDER BY clicks DESC LIMIT 30;

-- 3. Shortcuts used
SELECT json_extract(params, '$.code') AS key, count(*) FROM events WHERE event_name = 'ui_key' GROUP BY 1 ORDER BY 2 DESC;

-- 4. Settings changed and to what
SELECT json_extract(params, '$.control') AS setting, json_extract(params, '$.value') AS value, count(*)
FROM events WHERE event_name = 'ui_change' GROUP BY 1, 2 ORDER BY 3 DESC;

-- 5. Sessions: length in minutes, events, screens seen
SELECT session_id, datetime(min(event_timestamp) / 1e6, 'unixepoch') AS started,
       round((max(event_timestamp) - min(event_timestamp)) / 6e7, 1) AS minutes, count(*) AS events,
       group_concat(DISTINCT screen) AS screens
FROM events WHERE source = 'page' GROUP BY 1 ORDER BY started DESC;

-- 6. Time spent per screen (until the next event of the same session, capped at 5 minutes)
SELECT screen, round(sum(min(gap, 300e6)) / 6e7) AS minutes FROM (
  SELECT screen, lead(event_timestamp) OVER (PARTITION BY session_id ORDER BY event_timestamp) - event_timestamp AS gap
  FROM events WHERE source = 'page') WHERE gap IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;

-- 7. Errors in the last 30 days
SELECT event_name, source, coalesce(json_extract(params, '$.message'), json_extract(params, '$.code')) AS message,
       count(*), datetime(max(event_timestamp) / 1e6, 'unixepoch') AS last
FROM events WHERE level != 'info' AND event_date >= strftime('%Y%m%d', 'now', '-30 days')
GROUP BY 1, 2, 3 ORDER BY 4 DESC;

-- 8. Plugins: events and sessions that used them
SELECT plugin, count(*) AS events, count(DISTINCT session_id) AS sessions FROM events
WHERE plugin IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;

-- 9. Funnel per month: projects started, audio ready, stems ready, failed (with the app's database)
ATTACH 'C:/Users/<you>/Audio Stems/audio_stems.db' AS app;   -- macOS: '/Users/<you>/Audio Stems/audio_stems.db'
SELECT substr(created_at, 1, 7) AS month, input_type, count(*) AS started,
       sum(EXISTS (SELECT 1 FROM app.job_events e WHERE e.job_id = j.id AND json_extract(e.details_json, '$.status') = 'downloaded')) AS audio_ready,
       sum(status = 'completed') AS stems_ready, sum(status = 'failed') AS failed
FROM app.jobs j GROUP BY 1, 2 ORDER BY 1 DESC;

-- 10. Separation time by model (minutes per minute of song)
SELECT json_extract(separation_options_json, '$.model_name') AS model, json_extract(separation_options_json, '$.device') AS device,
       count(*), round(avg(separation_seconds) / 60, 1) AS avg_minutes,
       round(avg(separation_seconds / nullif(audio_seconds, 0)), 2) AS per_song_minute
FROM app.jobs WHERE status = 'completed' GROUP BY 1, 2;
```
