<script setup>
import { computed, onMounted, ref } from "vue";

const props = defineProps({
  client: { type: Object, required: true },
  permit: { type: Object, required: true },
});

const sql = ref("");
const offset = ref(0);
const result = ref(null); // { image, columns, rows_shown, truncated, notice }
const queryError = ref("");
const running = ref(false);
const blocked = ref("");
const outputs = ref([]);
const outputName = ref("");
const outputText = ref("");
const outputStatus = ref(null); // { type, text }

const pageRows = computed(() => props.permit.page_rows);
const tables = computed(() => Object.entries(props.permit.tables));
const baseSql = computed(() => sql.value.trim().replace(/;\s*$/, ""));
const hasLimit = computed(() => /\blimit\b/i.test(baseSql.value));

function browse(table) {
  sql.value = `SELECT * FROM ${table}`;
  run(0);
}

async function run(at = 0) {
  if (!baseSql.value) return;
  const paged = hasLimit.value ? baseSql.value : `${baseSql.value} LIMIT ${pageRows.value} OFFSET ${at}`;
  running.value = true;
  queryError.value = "";
  try {
    result.value = await props.client.query(paged);
    offset.value = at;
  } catch (e) {
    queryError.value = e.message;
    result.value = null;
  } finally {
    running.value = false;
  }
}

async function tryDownload() {
  blocked.value = "";
  try {
    await props.client.download();
  } catch (e) {
    blocked.value = `HTTP ${e.status} — ${e.message}`;
  }
}

async function loadOutputs() {
  outputs.value = await props.client.outputs();
}

async function submitOutput(kind) {
  outputStatus.value = null;
  const name = outputName.value.trim() || (kind === "sql" ? "query_result" : "text");
  try {
    const o = kind === "sql"
      ? await props.client.exportSql(name, baseSql.value)
      : await props.client.exportText(name, outputText.value);
    outputStatus.value = o.status === "released"
      ? { type: "success", text: `"${o.name}" passed the check and is released below.` }
      : { type: "error", text: `"${o.name}" refused: ${o.checks.join("; ")}` };
    await loadOutputs();
  } catch (e) {
    outputStatus.value = { type: "error", text: e.message };
  }
}

function contentText(o) {
  return typeof o.content === "string" ? o.content : JSON.stringify(o.content, null, 2);
}

function saveOutput(o) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([contentText(o)], { type: "application/json" }));
  a.download = `${props.permit.id}-output-${o.id}-${o.name}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

onMounted(loadOutputs);
</script>

<template>
  <v-container fluid class="pa-4">
    <v-row>
      <v-col cols="12">
        <v-card elevation="1">
          <v-card-title class="d-flex align-center ga-3 flex-wrap">
            <v-chip color="secondary" variant="flat" label>APPROVE_SPE</v-chip>
            <span>Art. {{ permit.articles.join(", ") }}</span>
            <span class="text-caption text-medium-emphasis">created {{ permit.created }}</span>
          </v-card-title>
          <v-card-text>
            <p class="mb-2">{{ permit.reason }}</p>
            <p class="mb-2">
              <strong>Applicant:</strong> {{ permit.applicant.name }}, {{ permit.applicant.organisation }} ({{ permit.applicant.country }})<br />
              <strong>Purpose:</strong> {{ permit.purpose }}
            </p>
            <p class="mb-0">
              <strong>Cohort:</strong> {{ permit.cohort_size.toLocaleString() }} patients (opted-out patients excluded, Art. 71).
              <strong>Records inside:</strong>{{ " " }}
              <span v-for="([t, n], i) in tables" :key="t">{{ i ? ", " : "" }}{{ t }} ({{ n.toLocaleString() }} rows)</span>.
              Patient IDs are pseudonyms and stay inside.
            </p>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="7">
        <v-card elevation="1">
          <v-card-title class="d-flex align-center flex-wrap ga-2">
            <span>Work with the data — inside the SPE</span>
            <v-spacer />
            <v-btn color="error" variant="outlined" size="small" prepend-icon="mdi-download-off" @click="tryDownload">
              Download records
            </v-btn>
          </v-card-title>
          <v-card-text>
            <v-alert v-if="blocked" type="error" variant="tonal" density="compact" class="mb-3" closable @click:close="blocked = ''">
              <strong>Blocked.</strong> {{ blocked }}
            </v-alert>
            <div class="d-flex align-center flex-wrap ga-2 mb-3">
              <span class="text-caption text-medium-emphasis">Browse:</span>
              <v-btn v-for="[t] in tables" :key="t" size="small" variant="tonal" color="primary" @click="browse(t)">{{ t }}</v-btn>
            </div>
            <v-textarea
              v-model="sql"
              label="SQL (read-only, one statement)"
              :placeholder="`SELECT icd10, COUNT(*) AS n FROM ${tables[0]?.[0] || 'conditions'} GROUP BY icd10 ORDER BY n DESC`"
              rows="3"
              auto-grow
              variant="outlined"
              density="compact"
              class="font-mono"
              @keydown.ctrl.enter.prevent="run(0)"
            />
            <div class="d-flex align-center flex-wrap ga-2 mb-2">
              <v-btn color="primary" size="small" :loading="running" prepend-icon="mdi-play" @click="run(0)">Run</v-btn>
              <v-btn size="small" variant="text" :disabled="offset === 0 || hasLimit" @click="run(Math.max(0, offset - pageRows))">‹ previous {{ pageRows }}</v-btn>
              <v-btn size="small" variant="text" :disabled="!result || !result.truncated && result.rows_shown < pageRows || hasLimit" @click="run(offset + pageRows)">next {{ pageRows }} ›</v-btn>
              <span v-if="result" class="text-caption text-medium-emphasis">
                {{ result.rows_shown }} rows shown (rows {{ offset + 1 }}–{{ offset + result.rows_shown }}) — {{ result.notice }}
              </span>
            </div>
            <v-alert v-if="queryError" type="error" variant="tonal" density="compact" class="mb-2">{{ queryError }}</v-alert>
            <div v-if="result" class="result-frame" @contextmenu.prevent @dragstart.prevent>
              <img :src="result.image" alt="query result, rendered inside the SPE" draggable="false" />
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" lg="5">
        <v-card elevation="1">
          <v-card-title>Outputs — the only way out</v-card-title>
          <v-card-text>
            <p class="text-caption text-medium-emphasis mb-3">
              The server runs your SQL itself and releases the table only if it has no patient IDs, no record-level
              columns or dates, no count below 10 and at most {{ permit.export_rows }} rows (Art. 73(1)(e)). Text is released if it
              contains no patient IDs.
            </p>
            <v-text-field v-model="outputName" label="Output name" placeholder="counts_by_icd10" variant="outlined" density="compact" />
            <v-btn color="primary" variant="tonal" size="small" block class="mb-3" :disabled="!baseSql" prepend-icon="mdi-table-arrow-right" @click="submitOutput('sql')">
              Release the result of the SQL above
            </v-btn>
            <v-textarea v-model="outputText" label="Or text / JSON (metrics, model card)" placeholder='{"auc": 0.81, "n_train": 920}' rows="2" auto-grow variant="outlined" density="compact" class="font-mono" />
            <v-btn color="primary" variant="tonal" size="small" block :disabled="!outputText.trim()" prepend-icon="mdi-text-box-check" @click="submitOutput('text')">
              Release text
            </v-btn>
            <v-alert v-if="outputStatus" :type="outputStatus.type" variant="tonal" density="compact" class="mt-3">{{ outputStatus.text }}</v-alert>

            <v-list v-if="outputs.length" density="compact" class="mt-3">
              <v-list-item v-for="o in outputs" :key="o.id">
                <template #prepend>
                  <v-chip size="x-small" label :color="o.status === 'released' ? 'success' : 'error'" variant="flat">{{ o.status }}</v-chip>
                </template>
                <v-list-item-title>#{{ o.id }} {{ o.name }} <span class="text-caption text-medium-emphasis">({{ o.kind }}, {{ o.at }})</span></v-list-item-title>
                <v-list-item-subtitle v-if="o.checks.length" class="text-error">{{ o.checks.join("; ") }}</v-list-item-subtitle>
                <v-expand-transition>
                  <pre v-if="o.status === 'released'" class="released">{{ contentText(o) }}</pre>
                </v-expand-transition>
                <template #append>
                  <v-btn v-if="o.status === 'released'" icon="mdi-download" size="small" variant="text" title="save this released output" @click="saveOutput(o)" />
                </template>
              </v-list-item>
            </v-list>
            <p v-else class="text-caption text-medium-emphasis mt-3">No outputs yet.</p>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<style scoped>
.font-mono :deep(textarea), .font-mono :deep(input) { font-family: ui-monospace, monospace; font-size: 13px; }
.result-frame { max-height: 520px; overflow: auto; border: 1px solid #ddd; border-radius: 4px; user-select: none; background: #fff; }
.result-frame img { display: block; max-width: none; pointer-events: none; }
.released { font: 12px/1.4 ui-monospace, monospace; background: #f7f7f7; border: 1px solid #e5e5e5; padding: 6px; margin: 4px 0; max-height: 200px; overflow: auto; white-space: pre-wrap; }
</style>
