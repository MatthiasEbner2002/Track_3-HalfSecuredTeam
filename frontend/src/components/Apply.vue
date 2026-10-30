<script setup>
import { computed, onMounted, ref } from "vue";

// What an applicant sees: the request form and the access body's answer. No test scaffolding.

const form = ref(blank());
const samples = ref([]);
const sending = ref(false);
const error = ref("");
const result = ref(null);
const copied = ref(false);
const loaded = ref(null); // which of the 10 task requests is in the form
const tab = ref("answer"); // answer | json

function blank() {
  return {
    id: "",
    applicant: { name: "", organisation: "", country: "AT" },
    requested_as: "health data request",
    requested_format: "anonymised statistics",
    purpose: "",
    question: "",
    definitions: "",
  };
}

const FORMATS = {
  "health data request": "anonymised statistics",
  "health data access application": "pseudonymised records",
};
function onKind(kind) {
  form.value.requested_as = kind;
  form.value.requested_format = FORMATS[kind];
}

async function loadSamples() {
  samples.value = (await (await fetch("/api/samples")).json()).filter((s) => s.id.startsWith("R"));
}
async function prefill(id) {
  if (!id) return;
  const { request } = await (await fetch(`/api/samples/${id}`)).json();
  form.value = { ...blank(), ...request, applicant: { ...request.applicant }, definitions: (request.definitions || []).join("\n") };
  loaded.value = id;
  result.value = null;
  error.value = "";
}

function clear() {
  form.value = blank();
  loaded.value = null;
  result.value = null;
  error.value = "";
}

const body = computed(() => ({
  id: form.value.id.trim() || `A-${Date.now().toString(36).toUpperCase()}`,
  submitted: new Date().toISOString(),
  applicant: form.value.applicant,
  requested_as: form.value.requested_as,
  requested_format: form.value.requested_format,
  purpose: form.value.purpose,
  question: form.value.question,
  definitions: form.value.definitions.split("\n").map((d) => d.trim()).filter(Boolean),
}));

async function submit() {
  error.value = "";
  result.value = null;
  sending.value = true;
  try {
    const res = await fetch("/api/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body.value) });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      error.value = typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail ?? json);
      return;
    }
    result.value = json;
    tab.value = "answer";
  } catch (e) {
    error.value = "Request failed: " + e.message;
  } finally {
    sending.value = false;
  }
}

const DECISION = {
  REJECT: { color: "error", icon: "mdi-close-circle-outline", title: "Rejected" },
  APPROVE_STATISTICAL: { color: "success", icon: "mdi-chart-box-outline", title: "Approved: anonymised statistics" },
  APPROVE_SPE: { color: "secondary", icon: "mdi-shield-lock-outline", title: "Approved: pseudonymised records in a secure processing environment" },
};

const table = computed(() => {
  const d = result.value?.data;
  if (!d || result.value.decision !== "APPROVE_STATISTICAL") return null;
  const entries = Object.entries(d);
  if (!entries.length) return null;
  const objects = entries.every(([, v]) => v && typeof v === "object" && !Array.isArray(v));
  const cols = objects ? [...new Set(entries.flatMap(([, v]) => Object.keys(v)))] : [];
  const rows = entries.map(([k, v]) => [k, ...(objects ? cols.map((c) => v[c] ?? "") : Array.isArray(v) ? v : [v])]);
  return { cols, rows };
});

const pending = computed(() => (result.value?.flags || []).some((f) => f.startsWith("not_interpreted")));

async function copy(text) {
  await navigator.clipboard.writeText(text);
  copied.value = true;
  setTimeout(() => (copied.value = false), 1500);
}

onMounted(loadSamples);
</script>

<template>
  <v-container class="py-6" style="max-width: 1100px">
    <v-row>
      <v-col cols="12" md="6">
        <v-card elevation="1" rounded="lg">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-file-document-edit-outline" class="mr-2" color="primary" />Request health data
            <v-spacer />
            <v-btn size="small" variant="text" prepend-icon="mdi-eraser" @click="clear">Clear</v-btn>
          </v-card-title>
          <v-card-text>
            <div class="text-caption text-medium-emphasis mb-1">Prefill with one of the 10 task requests</div>
            <div class="d-flex flex-wrap ga-1 mb-4">
              <v-btn v-for="s in samples" :key="s.id" size="small" :variant="loaded === s.id ? 'flat' : 'tonal'" :color="loaded === s.id ? 'warning' : 'primary'" :title="s.decision" @click="prefill(s.id)">{{ s.id }}</v-btn>
            </div>
            <v-form @submit.prevent="submit">
              <p class="text-overline mb-1">Applicant</p>
              <v-row dense>
                <v-col cols="12" sm="7"><v-text-field v-model="form.applicant.name" label="Name" variant="outlined" density="compact" /></v-col>
                <v-col cols="12" sm="5"><v-text-field v-model="form.applicant.country" label="Country" variant="outlined" density="compact" /></v-col>
                <v-col cols="12"><v-text-field v-model="form.applicant.organisation" label="Organisation and what it does" variant="outlined" density="compact" /></v-col>
              </v-row>
              <p class="text-overline mb-1 mt-2">What you ask for</p>
              <v-btn-toggle :model-value="form.requested_as" mandatory divided variant="outlined" color="primary" density="comfortable" class="mb-3 w-100" @update:model-value="onKind">
                <v-btn value="health data request" class="flex-grow-1" prepend-icon="mdi-chart-bar">Statistics</v-btn>
                <v-btn value="health data access application" class="flex-grow-1" prepend-icon="mdi-account-details-outline">Patient-level records</v-btn>
              </v-btn-toggle>
              <v-textarea v-model="form.purpose" label="Purpose — what you will do with the data and why" rows="3" auto-grow variant="outlined" density="compact" />
              <v-textarea v-model="form.question" label="Question — what you want to receive" rows="2" auto-grow variant="outlined" density="compact" />
              <v-textarea v-model="form.definitions" label="Definitions — one per line, in plain English" rows="4" auto-grow variant="outlined" density="compact" class="font-mono"
                          hint="e.g. Adult = aged 18 or older on 31 December 2025." persistent-hint />
              <v-text-field v-model="form.id" label="Your reference (optional)" variant="outlined" density="compact" class="mt-3" />
              <v-btn type="submit" color="primary" size="large" block :loading="sending" prepend-icon="mdi-send-outline">Submit request</v-btn>
            </v-form>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6">
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-card v-if="!result && !error" variant="tonal" color="primary" rounded="lg" class="pa-4">
          <div class="d-flex align-center mb-2"><v-icon icon="mdi-information-outline" class="mr-2" />How this is decided</div>
          <ol class="text-body-2 pl-5 mb-0">
            <li>A prohibited purpose (Art. 54) is rejected.</li>
            <li>A request for statistics is answered with anonymised numbers (Art. 53, 69), small groups suppressed.</li>
            <li>An application for records is granted only if it explains why statistics are not enough; then the data stays in a secure processing environment (Art. 66, 68, 73).</li>
            <li>Otherwise it is answered with statistics instead.</li>
          </ol>
        </v-card>

        <v-card v-if="result" elevation="1" rounded="lg">
          <v-sheet :color="DECISION[result.decision].color" class="pa-4 text-white d-flex align-center">
            <v-icon :icon="DECISION[result.decision].icon" size="32" class="mr-3" />
            <div>
              <div class="text-subtitle-1 font-weight-medium lh-1">{{ DECISION[result.decision].title }}</div>
              <div class="text-caption">Request {{ result.request_id }} · Art. {{ result.articles.join(", ") }} Regulation (EU) 2025/327</div>
            </div>
          </v-sheet>
          <v-tabs v-model="tab" color="primary" density="compact" class="border-b">
            <v-tab value="answer" prepend-icon="mdi-text-box-check-outline">Answer</v-tab>
            <v-tab value="json" prepend-icon="mdi-code-json">Raw JSON</v-tab>
          </v-tabs>
          <v-card-text v-if="tab === 'json'">
            <div class="d-flex justify-end mb-1">
              <v-btn size="small" variant="text" prepend-icon="mdi-content-copy" @click="copy(JSON.stringify(result, null, 2))">{{ copied ? "Copied" : "Copy" }}</v-btn>
            </div>
            <pre class="json">{{ JSON.stringify(result, null, 2) }}</pre>
          </v-card-text>
          <v-card-text v-else>
            <p class="text-body-1">{{ result.reason }}</p>
            <v-alert v-if="result.needs_review" type="warning" variant="tonal" density="compact" class="mb-3">
              This decision will be confirmed by a member of staff before it is final.
            </v-alert>
            <v-alert v-if="pending && result.decision !== 'REJECT'" type="info" variant="tonal" density="compact" class="mb-3">
              Your definitions could not be turned into a query automatically; staff will prepare the answer.
            </v-alert>

            <template v-if="table">
              <p class="text-overline mb-1">Your statistics</p>
              <v-table density="compact" class="mb-2">
                <thead v-if="table.cols.length"><tr><th></th><th v-for="c in table.cols" :key="c">{{ c.replaceAll("_", " ") }}</th></tr></thead>
                <tbody><tr v-for="r in table.rows" :key="r[0]"><td v-for="(v, i) in r" :key="i" :class="{ 'font-weight-medium': i === 0 }">{{ v }}</td></tr></tbody>
              </v-table>
              <p class="text-caption text-medium-emphasis">Counts below 10 are shown as "&lt;10"; a percentage or mean based on fewer than 10 patients is "suppressed". Patients who opted out are not included.</p>
            </template>

            <template v-if="result.decision === 'APPROVE_SPE' && result.data?.spe">
              <p class="text-overline mb-1">Your secure processing environment</p>
              <p class="mb-2">
                <strong>{{ result.data.cohort_size.toLocaleString() }}</strong> patients;
                records: <span v-for="(t, i) in result.data.record_types" :key="t">{{ i ? ", " : "" }}{{ t }}</span>.
                You can view and query them inside the environment; nothing can be downloaded.
              </p>
              <v-sheet color="grey-lighten-4" rounded class="pa-3 mb-2">
                <div class="text-caption text-medium-emphasis mb-1">Access key — shown once, valid until {{ result.data.spe.key_expires }}. Keep it together with your name.</div>
                <div class="d-flex align-center ga-2">
                  <code class="key flex-grow-1">{{ result.data.spe.access_key }}</code>
                  <v-btn size="small" variant="tonal" :icon="copied ? 'mdi-check' : 'mdi-content-copy'" title="copy" @click="copy(result.data.spe.access_key)" />
                </div>
              </v-sheet>
              <v-btn color="secondary" href="#/spe" prepend-icon="mdi-shield-lock-outline">Open the environment</v-btn>
            </template>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<style scoped>
.font-mono :deep(textarea) { font-family: ui-monospace, monospace; font-size: 13px; }
.key { font: 13px ui-monospace, monospace; background: #fff; border: 1px solid #ddd; padding: 6px 10px; border-radius: 4px; user-select: all; word-break: break-all; }
.lh-1 { line-height: 1.25; }
.json { font: 12px/1.45 ui-monospace, monospace; background: #f6f7f9; border: 1px solid #e3e6ea; border-radius: 4px; padding: 10px; max-height: 640px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
</style>
