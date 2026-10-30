<script setup>
import { ref } from "vue";

const props = defineProps({ open: { type: Function, required: true } }); // async, throws on a wrong key or name
const key = ref("");
const applicant = ref("");
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  if (!key.value.trim() || !applicant.value.trim()) {
    error.value = "Both the access key and your name are needed.";
    return;
  }
  busy.value = true;
  try {
    await props.open({ key: key.value, applicant: applicant.value });
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <v-container class="fill-height" style="max-width: 560px">
    <v-card class="w-100" elevation="2">
      <v-card-title class="text-h6">Enter your data permit</v-card-title>
      <v-card-subtitle class="text-wrap">
        An approved access application (Art. 68, 73 Regulation (EU) 2025/327) came with an access key, valid for
        one day. The key opens the permit's secure processing environment only together with the applicant's name as
        written on the application.
      </v-card-subtitle>
      <v-card-text>
        <v-form @submit.prevent="submit">
          <v-text-field
            v-model="applicant"
            label="Applicant name"
            placeholder="as on the application, e.g. Dr. Amir Hadžić"
            prepend-inner-icon="mdi-account"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
          />
          <v-text-field
            v-model="key"
            label="Access key"
            placeholder="R05.…"
            prepend-inner-icon="mdi-key"
            type="password"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
            class="font-mono"
          />
          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-4">{{ error }}</v-alert>
          <v-btn type="submit" color="primary" block :loading="busy" prepend-icon="mdi-shield-lock">Open the environment</v-btn>
        </v-form>
      </v-card-text>
      <v-card-text class="text-caption text-medium-emphasis">
        Inside, records are shown as images and cannot be downloaded. Every access is logged.
      </v-card-text>
    </v-card>
  </v-container>
</template>
