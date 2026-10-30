// Comparison of a pipeline result with the expected outcome (same rules as backend/tests/fixtures/e2e/run.py).

const r1 = (v) => Math.round(Number(v) * 10 + Number.EPSILON) / 10;
const sortedJoin = (xs) => [...(xs || [])].sort().join(",");

function sameValue(e, a) {
  if (typeof e === "string" || typeof a === "string") return e === a;
  if (typeof e !== "number" || typeof a !== "number") return false;
  return Number.isInteger(e) ? a === e : r1(a) === r1(e);
}

// R-samples and named results store {group: {metric: value}}, E-cases {group: [values]}; compare as value lists
const values = (v) => (Array.isArray(v) ? v : v && typeof v === "object" ? Object.values(v) : [v]);

export function compare(exp, r) {
  const problems = [];
  if (r.decision !== exp.decision) problems.push(`decision ${r.decision} (expected ${exp.decision})`);
  if (sortedJoin(r.articles) !== sortedJoin(exp.articles)) problems.push(`articles ${r.articles.join(",")} (expected ${exp.articles.join(",")})`);
  if (exp.decision === "REJECT" || r.decision === "REJECT") return problems;
  const ans = exp.answer;
  if (ans == null) return problems;
  if (ans.error === true) {
    const hasData = r.data && Object.keys(r.data).length;
    if (hasData || !(r.flags || []).some((f) => f.startsWith("not_interpreted"))) problems.push("answer: expected not_interpreted");
    return problems;
  }
  const d = r.data || {};
  if (exp.decision === "APPROVE_SPE") {
    if (d.cohort_size !== ans.cohort_size) problems.push(`cohort_size ${d.cohort_size} (expected ${ans.cohort_size})`);
    if (ans.record_types && sortedJoin(d.record_types) !== sortedJoin(ans.record_types)) problems.push("record_types differ");
    return problems;
  }
  const groups = Object.keys(d).filter((k) => k !== "spe");
  if (sortedJoin(groups) !== sortedJoin(Object.keys(ans))) {
    problems.push("answer wrong-groups");
    return problems;
  }
  for (const [k, ev] of Object.entries(ans)) {
    const e = values(ev), a = values(d[k]);
    if (a.length !== e.length || !e.every((x, i) => sameValue(x, a[i]))) {
      problems.push(`answer wrong-values (${k})`);
      return problems;
    }
  }
  return problems;
}

// "E001-E010 R05 R01-R03" -> case ids that exist
export function parseRange(text, ids) {
  const out = [];
  for (const tok of text.trim().split(/[\s,]+/).filter(Boolean)) {
    const m = tok.match(/^([RE])(\d+)-([RE])(\d+)$/);
    if (m) {
      const lo = +m[2], hi = +m[4];
      ids.filter((i) => i[0] === m[1] && +i.slice(1) >= lo && +i.slice(1) <= hi).forEach((i) => out.push(i));
    } else if (ids.includes(tok)) out.push(tok);
  }
  return [...new Set(out)];
}

export const TEMPLATE = () => ({
  id: "MY01",
  submitted: new Date().toISOString(),
  applicant: { name: "", organisation: "", country: "AT" },
  requested_as: "health data request",
  requested_format: "anonymised statistics",
  purpose: "",
  question: "",
  definitions: [""],
});
