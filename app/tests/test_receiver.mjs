import assert from "node:assert/strict";
import test from "node:test";

import receiver from "../receiver/worker.js";

function fakeDb(countToday = 0) {
  const rows = [];
  return {
    rows,
    prepare(sql) {
      return {
        bind(...values) {
          return {
            first: async () => ({ n: countToday }),
            run: async () => rows.push({ sql, values }),
          };
        },
      };
    },
  };
}

const post = (body, path = "/v1/report") =>
  new Request(`https://receiver.example${path}`, { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) });

const report = { kind: "error_report", version: "25", platform: "windows", stage: "separating", message: "CUDA out of memory" };

test("a valid report is stored with the day only and its fields", async () => {
  const env = { DB: fakeDb() };
  const response = await receiver.fetch(post(report), env);
  assert.equal(response.status, 204);
  const [insert] = env.DB.rows;
  assert.match(insert.values[0], /^\d{4}-\d{2}-\d{2}$/);
  assert.deepEqual(insert.values.slice(1), ["error_report", "25", "windows", "separating", "CUDA out of memory"]);
});

test("invalid reports, other paths and methods, big bodies and a full day are refused and nothing is stored", async () => {
  const cases = [
    [post({ ...report, kind: "hello" }), 400],
    [post({ ...report, version: "v25" }), 400],
    [post({ ...report, platform: "linux" }), 400],
    [post({ ...report, stage: "Stage With Spaces" }), 400],
    [post({ ...report, message: "x".repeat(501) }), 400],
    [post({ ...report, email: "someone@example.com" }), 400],
    [post("not json"), 400],
    [post("x".repeat(3000)), 413],
    [post(report, "/v1/other"), 404],
    [new Request("https://receiver.example/v1/report"), 405],
  ];
  for (const [request, status] of cases) {
    const env = { DB: fakeDb() };
    assert.equal((await receiver.fetch(request, env)).status, status);
    assert.equal(env.DB.rows.length, 0);
  }
  const full = { DB: fakeDb(1000) };
  assert.equal((await receiver.fetch(post(report), full)).status, 429);
  assert.equal(full.DB.rows.length, 0);
});
