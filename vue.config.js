const { defineConfig } = require("@vue/cli-service")

// No `configureWebpack.plugins` any more: this file used to install
// webpack-plugin-vuetify (Vuetify's on-demand component/style loader), which
// existed solely to serve the single `<v-app>` wrapper sol.vue used to have.
// Vuetify was removed on 2026-09-02 — see the note in src/main.ts.
module.exports = defineConfig({
  publicPath: "./",
  productionSourceMap: false,

  // Needed for testing on real phones over the LAN. This makes the dev server
  // insecure, but that's OK since we only use it in controlled circumstances.
  devServer: {
    allowedHosts: 'all',
    client: {
      overlay: false
    }
  }
});
