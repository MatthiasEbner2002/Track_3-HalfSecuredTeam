<script setup>
import { computed, ref } from "vue";

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
</script>

<template>
  <v-container fluid class="pa-4">
    <v-row>
      <v-col cols="12">
        <v-card elevation="1">
          <v-card-title class="d-flex align-center ga-3 flex-wrap">
            <v-chip color="secondary" variant="flat" label>APPROVE_SPE</v-chip>
            <span>Art. {{ permit.articles.join(", ") }}</span>
            <span class="text-caption text-medium-emphasis">created {{ permit.created }} · key valid until {{ permit.key_expires }}</span>
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

      <v-col cols="12">
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

    </v-row>
  </v-container>
</template>

<style scoped>
.font-mono :deep(textarea), .font-mono :deep(input) { font-family: ui-monospace, monospace; font-size: 13px; }
.result-frame { max-height: 520px; overflow: auto; border: 1px solid #ddd; border-radius: 4px; user-select: none; background: #fff; }
.result-frame img { display: block; max-width: none; pointer-events: none; }
</style>
