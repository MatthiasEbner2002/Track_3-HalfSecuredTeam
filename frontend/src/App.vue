<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { makeClient } from "./api.js";
import Console from "./components/Console.vue";
import EnterKey from "./components/EnterKey.vue";
import Workspace from "./components/Workspace.vue";

// Two pages, hash-routed: #/spe (the applicant's SPE, default) and #/console (the access body's test console).
const page = ref("spe");
const readHash = () => (page.value = location.hash === "#/console" ? "console" : "spe");
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
    <v-app-bar color="primary" density="comfortable" flat>
      <v-app-bar-title>
        <template v-if="page === 'console'">EHDS Health Data Access Body — test console</template>
        <template v-else>Secure processing environment <span v-if="permit" class="font-weight-bold">{{ permit.id }}</span></template>
      </v-app-bar-title>
      <template #append>
        <template v-if="page === 'spe' && permit">
          <span class="text-body-2 mr-4">{{ permit.applicant.name }}</span>
          <v-btn variant="outlined" size="small" prepend-icon="mdi-logout" class="mr-4" @click="leave">Leave</v-btn>
        </template>
        <v-btn-toggle :model-value="page" density="compact" variant="outlined" mandatory @update:model-value="(p) => (location.hash = `#/${p}`)">
          <v-btn value="console" size="small" prepend-icon="mdi-console">Console</v-btn>
          <v-btn value="spe" size="small" prepend-icon="mdi-shield-lock">SPE</v-btn>
        </v-btn-toggle>
      </template>
    </v-app-bar>
    <v-main class="bg-grey-lighten-4">
      <Console v-if="page === 'console'" />
      <template v-else>
        <EnterKey v-if="!client" :open="open" />
        <Workspace v-else :client="client" :permit="permit" />
      </template>
    </v-main>
  </v-app>
</template>
