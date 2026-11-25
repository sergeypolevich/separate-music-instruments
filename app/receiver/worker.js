// The report receiver (Cloudflare Worker + D1 in the EU). It keeps only what the report carries: no IP address,
// no identifier, no cookie, and the day without the time. See PRIVACY.md at the repository root.
const KINDS = new Set(["install_ok", "install_failed", "error_report"]);
const PLATFORMS = new Set(["windows", "macos"]);
const MAX_BODY = 2048;
const DAILY_LIMIT = 1000; // all senders together: a flood fills one day, not the free plan's quota

const answer = (status, text = "") => new Response(text || null, { status });

export function checkReport(report) {
  if (!report || typeof report !== "object" || Array.isArray(report)) return "not an object";
  const { kind, version, platform, stage = "", message = "" } = report;
  if (!KINDS.has(kind)) return "kind";
  if (typeof version !== "string" || !/^\d{1,4}$/.test(version)) return "version";
  if (!PLATFORMS.has(platform)) return "platform";
  if (typeof stage !== "string" || !/^[a-z_]{0,40}$/.test(stage)) return "stage";
  if (typeof message !== "string" || message.length > 500) return "message";
  const extra = Object.keys(report).filter((key) => !["kind", "version", "platform", "stage", "message"].includes(key));
  return extra.length ? "unknown field" : "";
}

export default {
  async fetch(request, env) {
    if (new URL(request.url).pathname !== "/v1/report") return answer(404);
    if (request.method !== "POST") return answer(405);
    const body = await request.text();
    if (body.length > MAX_BODY) return answer(413);
    let report;
    try {
      report = JSON.parse(body);
    } catch {
      return answer(400, "invalid JSON");
    }
    const wrong = checkReport(report);
    if (wrong) return answer(400, `invalid ${wrong}`);
    const day = new Date().toISOString().slice(0, 10);
    const today = await env.DB.prepare("SELECT count(*) AS n FROM reports WHERE day = ?").bind(day).first();
    if (today.n >= DAILY_LIMIT) return answer(429);
    await env.DB.prepare("INSERT INTO reports (day, kind, version, platform, stage, message) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(day, report.kind, report.version, report.platform, report.stage || "", report.message || "")
      .run();
    return answer(204);
  },
};
