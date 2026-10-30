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
        <v-card elevation="1" rounded="lg">
          <v-sheet color="secondary" class="pa-4 text-white d-flex align-center flex-wrap ga-4">
            <v-icon icon="mdi-shield-check-outline" size="32" />
            <div class="flex-grow-1">
              <div class="text-subtitle-1 font-weight-medium lh-1">Data permit {{ permit.id }} — pseudonymised records in a secure processing environment</div>
              <div class="text-caption">Art. {{ permit.articles.join(", ") }} Regulation (EU) 2025/327 · issued {{ permit.created }} · key valid until {{ permit.key_expires }}</div>
            </div>
            <div class="text-center px-3">
              <div class="text-h5 font-weight-bold lh-1">{{ permit.cohort_size.toLocaleString() }}</div>
              <div class="text-caption">patients</div>
            </div>
          </v-sheet>
          <v-card-text>
            <p class="mb-3">{{ permit.reason }}</p>
            <v-row dense class="text-body-2">
              <v-col cols="12" md="6">
                <div class="text-overline">Applicant</div>
                {{ permit.applicant.name }}, {{ permit.applicant.organisation }} ({{ permit.applicant.country }})
                <div class="text-overline mt-2">Purpose</div>
                {{ permit.purpose }}
              </v-col>
              <v-col cols="12" md="6">
                <div class="text-overline">Records inside</div>
                <v-chip v-for="[t, n] in tables" :key="t" size="small" variant="tonal" color="primary" class="mr-1 mb-1" prepend-icon="mdi-table">{{ t }} · {{ n.toLocaleString() }} rows</v-chip>
                <div class="text-caption text-medium-emphasis mt-1">Opted-out patients are excluded (Art. 71). Patient IDs are pseudonyms and stay inside.</div>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12">
        <v-card elevation="1" rounded="lg">
          <v-card-title class="d-flex align-center flex-wrap ga-2">
            <v-icon icon="mdi-database-search-outline" color="primary" class="mr-1" /><span>Work with the data — inside the SPE</span>
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
.lh-1 { line-height: 1.25; }
.result-frame { max-height: 520px; overflow: auto; border: 1px solid #ddd; border-radius: 4px; user-select: none; background: #fff; }
.result-frame img { display: block; max-width: none; pointer-events: none; }
</style>
