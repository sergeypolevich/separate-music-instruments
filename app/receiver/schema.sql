CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  version TEXT NOT NULL,
  platform TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS reports_by_day ON reports (day);
