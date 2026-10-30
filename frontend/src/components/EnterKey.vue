<script setup>
import { ref } from "vue";

const props = defineProps({
  open: { type: Function, required: true }, // async, throws on a wrong key or name
  prefill: { type: Object, default: null }, // { key, applicant } when coming straight from an approved application
});
const key = ref(props.prefill?.key ?? "");
const applicant = ref(props.prefill?.applicant ?? "");
const showKey = ref(false);
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
  <div class="login-page d-flex justify-center pa-4">
    <div class="login mx-auto">
      <div class="text-center mb-6">
        <v-avatar color="primary" size="64" class="mb-3 elevation-2"><v-icon icon="mdi-shield-lock-outline" size="34" /></v-avatar>
        <h1 class="text-h5 font-weight-medium mb-1">Enter your data permit</h1>
        <p class="text-body-2 text-medium-emphasis mb-0">
          Your approved access application (Art. 68, 73) came with an access key, valid for one day. It opens the
          permit's secure processing environment together with the applicant's name as written on the application.
        </p>
      </div>
      <v-card elevation="3" rounded="lg">
        <v-card-text class="pa-6">
          <v-form @submit.prevent="submit">
            <v-text-field
              v-model="applicant"
              label="Applicant name"
              placeholder="as on the application"
              prepend-inner-icon="mdi-account-outline"
              autocomplete="off"
              variant="outlined"
              class="mb-2"
            />
            <v-text-field
              v-model="key"
              label="Access key"
              placeholder="R05.…"
              prepend-inner-icon="mdi-key-outline"
              :append-inner-icon="showKey ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
              :type="showKey ? 'text' : 'password'"
              autocomplete="off"
              variant="outlined"
              class="font-mono mb-2"
              @click:append-inner="showKey = !showKey"
            />
            <v-expand-transition>
              <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-4">{{ error }}</v-alert>
            </v-expand-transition>
            <v-btn type="submit" color="primary" size="large" block :loading="busy" prepend-icon="mdi-login-variant">Open the environment</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
      <div class="hints d-flex justify-center flex-wrap ga-6 mt-6 text-caption text-medium-emphasis text-no-wrap">
        <span><v-icon icon="mdi-image-outline" size="16" class="mr-1" />records shown as images</span>
        <span><v-icon icon="mdi-download-off-outline" size="16" class="mr-1" />no download</span>
        <span><v-icon icon="mdi-clipboard-text-clock-outline" size="16" class="mr-1" />every access logged</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page { width: 100%; min-height: 100%; padding-top: 32px !important; }
.login { width: 100%; max-width: 520px; }
/* May be wider than the card so the three hints share one line; stays centred when they wrap. */
.hints { width: max-content; max-width: calc(100vw - 32px); position: relative; left: 50%; transform: translateX(-50%); }
.font-mono :deep(input) { font-family: ui-monospace, monospace; letter-spacing: 0.02em; }
</style>
