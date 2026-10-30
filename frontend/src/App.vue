<script setup>
import { ref } from "vue";
import { makeClient } from "./api.js";
import EnterKey from "./components/EnterKey.vue";
import Workspace from "./components/Workspace.vue";

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
        Secure processing environment
        <span v-if="permit" class="font-weight-bold">{{ permit.id }}</span>
      </v-app-bar-title>
      <template #append>
        <span v-if="permit" class="text-body-2 mr-4">{{ permit.applicant.name }}</span>
        <v-btn v-if="permit" variant="outlined" size="small" prepend-icon="mdi-logout" @click="leave">Leave</v-btn>
      </template>
    </v-app-bar>
    <v-main class="bg-grey-lighten-4">
      <EnterKey v-if="!client" :open="open" />
      <Workspace v-else :client="client" :permit="permit" />
    </v-main>
  </v-app>
</template>
