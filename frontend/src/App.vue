<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { makeClient } from "./api.js";
import Apply from "./components/Apply.vue";
import Console from "./components/Console.vue";
import EnterKey from "./components/EnterKey.vue";
import Workspace from "./components/Workspace.vue";

// Hash-routed pages: #/apply (applicant submits a request and reads the answer), #/spe (the applicant's
// secure processing environment) and #/console (the access body's test console with expected outcomes).
const PAGES = [
  { id: "apply", label: "Request", icon: "mdi-file-document-edit-outline", href: "#/apply", title: "Health Data Access Body — request" },
  { id: "spe", label: "SPE", icon: "mdi-shield-lock-outline", href: "#/spe", title: "Secure processing environment" },
  { id: "console", label: "Test console", icon: "mdi-console-line", href: "#/console", title: "Health Data Access Body — test console" },
];
const page = ref("apply");
const readHash = () => {
  const id = location.hash.replace(/^#\//, "");
  page.value = PAGES.some((p) => p.id === id) ? id : "apply";
};
onMounted(() => { readHash(); addEventListener("hashchange", readHash); });
onUnmounted(() => removeEventListener("hashchange", readHash));

const client = ref(null);
const permit = ref(null);

async function open({ key, applicant }) {
  const c = makeClient(key, applicant);
  permit.value = await c.access(); // throws ApiError on a wrong key or name
  client.value = c;
}

function leave() {
  client.value = null;
  permit.value = null;
}
</script>

<template>
  <v-app>
    <v-app-bar color="primary" density="comfortable" flat class="px-2">
      <template #prepend>
        <v-icon icon="mdi-hospital-box-outline" size="28" class="ml-1" />
      </template>
      <v-app-bar-title>
        <div class="d-flex flex-column">
          <span class="text-subtitle-1 font-weight-medium lh-1">
            {{ PAGES.find((p) => p.id === page).title }}<span v-if="page === 'spe' && permit"> · {{ permit.id }}</span>
          </span>
          <span class="text-caption text-blue-lighten-4 lh-1">EHDS · Regulation (EU) 2025/327</span>
        </div>
      </v-app-bar-title>
      <template #append>
        <template v-if="page === 'spe' && permit">
          <v-chip variant="tonal" color="white" size="small" prepend-icon="mdi-account" class="mr-2">{{ permit.applicant.name }}</v-chip>
          <v-btn variant="text" size="small" prepend-icon="mdi-logout" class="mr-4" @click="leave">Leave</v-btn>
        </template>
        <nav class="nav">
          <a v-for="p in PAGES" :key="p.id" :href="p.href" class="nav-link" :class="{ active: page === p.id }">
            <v-icon :icon="p.icon" size="18" class="mr-1" />{{ p.label }}
          </a>
        </nav>
      </template>
    </v-app-bar>

    <v-main class="page">
      <Apply v-if="page === 'apply'" />
      <Console v-else-if="page === 'console'" />
      <template v-else>
        <EnterKey v-if="!client" :open="open" />
        <Workspace v-else :client="client" :permit="permit" />
      </template>
    </v-main>

    <v-footer app class="justify-center text-caption text-medium-emphasis bg-transparent py-1">
      HalfSecuredTeam · Track 3 · synthetic data only
    </v-footer>
  </v-app>
</template>

<style>
.page { background: linear-gradient(180deg, #eef2f7 0%, #f6f7f9 240px); min-height: 100%; }
.lh-1 { line-height: 1.25; }
.nav { display: flex; gap: 4px; padding: 3px; border-radius: 999px; background: rgba(255, 255, 255, 0.12); }
.nav-link { display: inline-flex; align-items: center; padding: 5px 14px; border-radius: 999px; color: #dbe6f3; text-decoration: none; font-size: 13px; font-weight: 500; transition: background 0.15s, color 0.15s; }
.nav-link:hover { background: rgba(255, 255, 255, 0.14); color: #fff; }
.nav-link.active { background: #fff; color: #1f3a5f; }
.v-card { border: 1px solid rgba(31, 58, 95, 0.08) !important; }
</style>
