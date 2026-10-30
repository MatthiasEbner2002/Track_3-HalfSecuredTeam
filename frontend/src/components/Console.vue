<script setup>
import { computed, onMounted, ref } from "vue";
import { compare, parseRange, TEMPLATE } from "../compare.js";

const samples = ref([]);
const current = ref(null);
const expected = ref(null);
const note = ref("");
const input = ref(JSON.stringify(TEMPLATE(), null, 2));
const status = ref("");
const error = ref("");
const result = ref(null);
const lastResults = {};
const range = ref("E001-E010");
const batch = ref([]); // [{id, expected, got, result, state, secs}]
const tally = ref("Each request costs one LLM call (~5 s with a GPU, minutes on CPU). Rows are clickable.");
const batchRunning = ref(false);
let stopFlag = false;

const rSamples = computed(() => samples.value.filter((s) => s.id.startsWith("R")));
const eSamples = computed(() => samples.value.filter((s) => s.id.startsWith("E")));
const ids = computed(() => samples.value.map((s) => s.id));
const problems = computed(() => (expected.value && result.value ? compare(expected.value, result.value) : null));
const dataRows = computed(() => {
  const d = result.value?.data;
  if (!d) return null;
  const entries = Object.entries(d).filter(([k]) => k !== "spe");
  if (!entries.length) return null;
  if (entries.every(([, v]) => v && typeof v === "object" && !Array.isArray(v))) {
    const cols = [...new Set(entries.flatMap(([, v]) => Object.keys(v)))];
    return { cols, rows: entries.map(([k, v]) => [k, ...cols.map((c) => v[c] ?? "")]) };
  }
  if (entries.every(([, v]) => Array.isArray(v))) {
    return { cols: [], rows: entries.map(([k, v]) => [k, ...v]) };
  }
  return { cols: [], rows: entries.map(([k, v]) => [k, JSON.stringify(v)]) };
});

async function loadSamples() {
  samples.value = await (await fetch("/api/samples")).json();
}

async function loadSample(id) {
  current.value = id;
  const s = await (await fetch(`/api/samples/${id}`)).json();
  input.value = JSON.stringify(s.request, null, 2);
  expected.value = s.expected;
  note.value = s.note || "";
  error.value = "";
  result.value = lastResults[id] || null;
  status.value = result.value ? "from the last run" : "";
}

function step(d) {
  const i = ids.value.indexOf(current.value);
  loadSample(ids.value[i < 0 ? 0 : Math.min(ids.value.length - 1, Math.max(0, i + d))]);
}

function blank() {
  current.value = null;
  expected.value = null;
  note.value = "";
  input.value = JSON.stringify(TEMPLATE(), null, 2);
  result.value = null;
}

async function post(body) {
  const t0 = performance.now();
  const res = await fetch("/api/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = text; }
  return { res, json, secs: (performance.now() - t0) / 1000 };
}

const sending = ref(false);
async function send() {
  let body;
  try { body = JSON.parse(input.value); } catch (e) { error.value = "Invalid JSON: " + e.message; return; }
  error.value = "";
  sending.value = true;
  status.value = "running…";
  try {
    const { res, json, secs } = await post(body);
    status.value = `${res.status} in ${secs.toFixed(1)} s`;
    if (!res.ok) {
      error.value = typeof json === "string" ? json : JSON.stringify(json.detail ?? json, null, 2);
      result.value = null;
      return;
    }
    if (current.value) lastResults[current.value] = json;
    result.value = json;
  } catch (e) {
    error.value = "Request failed: " + e.message;
    status.value = "";
  } finally {
    sending.value = false;
  }
}

async function runBatch(list) {
  if (!list.length) { tally.value = "no matching case ids"; return; }
  stopFlag = false;
  batchRunning.value = true;
  batch.value = list.map((id) => ({ id, expected: "", got: "", result: "queued", state: "", secs: "" }));
  let pass = 0, fail = 0, done = 0;
  const t0 = performance.now();
  for (const row of batch.value) {
    if (stopFlag) break;
    row.state = "RUN"; row.result = "running…";
    try {
      const s = await (await fetch(`/api/samples/${row.id}`)).json();
      const { res, json, secs } = await post(s.request);
      row.expected = `${s.expected.decision} ${s.expected.articles.join(",")}`;
      row.secs = secs.toFixed(0) + " s";
      if (!res.ok) { row.state = "FAIL"; row.result = `HTTP ${res.status}`; fail++; }
      else {
        lastResults[row.id] = json;
        const p = compare(s.expected, json);
        row.got = `${json.decision} ${json.articles.join(",")}` + (json.flags?.length ? ` [${json.flags.join("; ").slice(0, 60)}]` : "");
        row.state = p.length ? "FAIL" : "PASS";
        row.result = p.length ? p.join("; ") : "PASS";
        p.length ? fail++ : pass++;
      }
    } catch (e) { row.state = "FAIL"; row.result = e.message; fail++; }
    done++;
    const avg = (performance.now() - t0) / 1000 / done;
    tally.value = `${done}/${list.length} done — pass ${pass}, fail ${fail} — avg ${avg.toFixed(0)} s/case, ~${Math.round((avg * (list.length - done)) / 60)} min left`;
  }
  if (stopFlag) tally.value += " — stopped";
  batchRunning.value = false;
}

const stateColor = { PASS: "success", FAIL: "error", RUN: "warning" };
const decisionColor = { REJECT: "error", APPROVE_STATISTICAL: "success", APPROVE_SPE: "secondary" };

onMounted(() => loadSamples().catch((e) => (error.value = "could not load samples: " + e.message)));
</script>

<template>
  <v-container fluid class="pa-4">
    <v-row>
      <v-col cols="12" lg="6">
        <v-card elevation="1">
          <v-card-title>Request</v-card-title>
          <v-card-text>
            <div class="d-flex flex-wrap ga-1 mb-3">
              <v-btn v-for="s in rSamples" :key="s.id" size="small" :variant="current === s.id ? 'flat' : 'tonal'" :color="current === s.id ? 'warning' : 'primary'" :title="s.decision" @click="loadSample(s.id)">{{ s.id }}</v-btn>
            </div>
            <div class="d-flex align-center ga-2 mb-3">
              <v-select
                :model-value="current?.startsWith('E') ? current : null"
                :items="eSamples.map((s) => ({ value: s.id, title: `${s.id}  ${s.decision}  — ${s.note || ''}` }))"
                label="Black-box case E001…E200" density="compact" variant="outlined" hide-details clearable
                @update:model-value="(v) => v && loadSample(v)"
              />
              <v-btn icon="mdi-chevron-left" size="small" variant="tonal" title="previous case" @click="step(-1)" />
              <v-btn icon="mdi-chevron-right" size="small" variant="tonal" title="next case" @click="step(1)" />
            </div>
            <div class="d-flex align-center ga-2 mb-3">
              <v-btn color="primary" :loading="sending" prepend-icon="mdi-send" @click="send">Send request</v-btn>
              <v-btn variant="tonal" color="primary" @click="blank">Blank template</v-btn>
              <span class="text-caption text-medium-emphasis">{{ status }}</span>
            </div>
            <v-textarea v-model="input" rows="22" variant="outlined" density="compact" class="font-mono" spellcheck="false" hide-details />
            <p v-if="expected" class="text-caption text-medium-emphasis mt-2 mb-0">
              Expected: {{ expected.decision }} — Art. {{ expected.articles.join(", ") }}<span v-if="expected.point"> ({{ expected.point }})</span><span v-if="note"> — {{ note }}</span>
            </p>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="6">
        <v-card elevation="1">
          <v-card-title>Result</v-card-title>
          <v-card-text>
            <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3"><pre class="pre">{{ error }}</pre></v-alert>
            <p v-if="!result && !error" class="text-medium-emphasis">Pick a sample or write your own request, then send.</p>
            <template v-if="result">
              <div class="d-flex align-center flex-wrap ga-3 mb-2">
                <v-chip :color="decisionColor[result.decision]" variant="flat" label>{{ result.decision }}</v-chip>
                <span>Art. {{ result.articles.join(", ") }}</span>
                <span v-if="result.route" class="text-caption text-medium-emphasis">route: {{ result.route }}</span>
                <v-chip v-if="result.needs_review" color="error" size="small" variant="tonal">needs review</v-chip>
              </div>
              <v-alert v-if="problems" :type="problems.length ? 'error' : 'success'" variant="tonal" density="compact" class="mb-3">
                {{ problems.length ? "MISMATCH: " + problems.join("; ") : `matches expected (decision, articles${expected.answer ? ", answer" : ""})` }}
              </v-alert>
              <p>{{ result.reason }}</p>
              <p v-if="result.flags?.length" class="text-caption text-medium-emphasis">flags: {{ result.flags.join(", ") }}</p>

              <v-expansion-panels variant="accordion" multiple :model-value="['evidence', 'data']">
                <v-expansion-panel v-if="result.evidence?.length" value="evidence" :title="`Evidence (${result.evidence.length})`">
                  <v-expansion-panel-text>
                    <v-table density="compact">
                      <thead><tr><th>Point</th><th>Label</th><th>Phrase</th><th>Source</th></tr></thead>
                      <tbody><tr v-for="(e, i) in result.evidence" :key="i"><td>{{ e.point }}</td><td>{{ e.label }}</td><td>{{ e.phrase }}</td><td>{{ e.source }}{{ e.score != null ? " " + e.score.toFixed(2) : "" }}</td></tr></tbody>
                    </v-table>
                  </v-expansion-panel-text>
                </v-expansion-panel>

                <v-expansion-panel v-if="result.data?.spe" value="data" title="Secure processing environment">
                  <v-expansion-panel-text>
                    <p><strong>SPE created</strong> — {{ result.data.cohort_size }} patients; records:
                      <span v-for="([t, n], i) in Object.entries(result.data.spe.tables)" :key="t">{{ i ? ", " : "" }}{{ t }}: {{ n }}</span>.</p>
                    <p class="mb-1">Access key for the applicant (shown once, valid until {{ result.data.spe.key_expires }}):</p>
                    <code class="key">{{ result.data.spe.access_key }}</code>
                    <p class="mt-2 mb-0">Applicant name to enter: <strong>{{ JSON.parse(input).applicant?.name }}</strong> —
                      <a href="#/spe" target="_blank">open the SPE</a></p>
                  </v-expansion-panel-text>
                </v-expansion-panel>
                <v-expansion-panel v-else-if="dataRows" value="data" title="Data">
                  <v-expansion-panel-text>
                    <v-table density="compact">
                      <thead v-if="dataRows.cols.length"><tr><th></th><th v-for="c in dataRows.cols" :key="c">{{ c }}</th></tr></thead>
                      <tbody><tr v-for="r in dataRows.rows" :key="r[0]"><td v-for="(v, i) in r" :key="i">{{ v }}</td></tr></tbody>
                    </v-table>
                  </v-expansion-panel-text>
                </v-expansion-panel>

                <v-expansion-panel v-if="expected?.answer && result.decision !== 'REJECT'" value="expected" title="Expected answer">
                  <v-expansion-panel-text><pre class="pre">{{ JSON.stringify(expected.answer, null, 2) }}</pre></v-expansion-panel-text>
                </v-expansion-panel>
                <v-expansion-panel v-if="result.queries?.length" value="sql" :title="`SQL (${result.queries.length})`">
                  <v-expansion-panel-text><pre class="pre">{{ result.queries.map((q) => `-- ${q.name}\n${q.sql}\n-- params: ${JSON.stringify(q.params)}`).join("\n\n") }}</pre></v-expansion-panel-text>
                </v-expansion-panel>
                <v-expansion-panel value="json" title="Full JSON response">
                  <v-expansion-panel-text><pre class="pre">{{ JSON.stringify(result, null, 2) }}</pre></v-expansion-panel-text>
                </v-expansion-panel>
              </v-expansion-panels>
            </template>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card elevation="1">
          <v-card-title>Batch run</v-card-title>
          <v-card-text>
            <div class="d-flex align-center flex-wrap ga-2 mb-2">
              <v-text-field v-model="range" label="Cases" title="ids or ranges, e.g. E001-E020 E150 R01-R10" density="compact" variant="outlined" hide-details style="max-width: 260px" />
              <v-btn color="primary" :disabled="batchRunning" @click="runBatch(parseRange(range, ids))">Run</v-btn>
              <v-btn variant="tonal" color="primary" :disabled="batchRunning || !batch.some((r) => r.state === 'FAIL')" @click="runBatch(batch.filter((r) => r.state === 'FAIL').map((r) => r.id))">Re-run failed</v-btn>
              <v-btn variant="tonal" :disabled="!batchRunning" @click="stopFlag = true">Stop</v-btn>
              <span class="text-caption text-medium-emphasis">{{ tally }}</span>
            </div>
            <v-table v-if="batch.length" density="compact" class="batch">
              <thead><tr><th>Case</th><th>Expected</th><th>Got</th><th>Result</th><th>Time</th></tr></thead>
              <tbody>
                <tr v-for="r in batch" :key="r.id" style="cursor: pointer" @click="loadSample(r.id)">
                  <td><v-chip v-if="r.state" size="x-small" :color="stateColor[r.state]" variant="flat" label class="mr-2">{{ r.state }}</v-chip>{{ r.id }}</td>
                  <td>{{ r.expected }}</td><td>{{ r.got }}</td>
                  <td :class="r.state === 'FAIL' ? 'text-error' : r.state === 'PASS' ? 'text-success' : 'text-medium-emphasis'">{{ r.result }}</td>
                  <td>{{ r.secs }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<style scoped>
.font-mono :deep(textarea) { font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.4; }
.pre { font: 12px/1.4 ui-monospace, monospace; white-space: pre-wrap; margin: 0; max-height: 420px; overflow: auto; }
.key { font: 13px ui-monospace, monospace; background: #f1f3f6; padding: 4px 8px; border-radius: 4px; user-select: all; }
.batch { max-height: 420px; overflow: auto; }
</style>
