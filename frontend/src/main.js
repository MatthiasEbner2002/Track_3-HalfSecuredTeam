import { createApp } from "vue";
import { createVuetify } from "vuetify";
import "vuetify/styles";
import "@mdi/font/css/materialdesignicons.css";
import App from "./App.vue";

const vuetify = createVuetify({
  theme: {
    defaultTheme: "light",
    themes: {
      light: { colors: { primary: "#1f3a5f", secondary: "#2980b9", error: "#c0392b", success: "#27ae60", warning: "#e8a33d" } },
    },
  },
});

createApp(App).use(vuetify).mount("#app");
