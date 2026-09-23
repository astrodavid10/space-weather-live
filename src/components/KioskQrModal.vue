<template>
  <!-- Full-viewport backdrop. NOT teleported: nesting inside #app keeps the
       --accent-color CSS custom property resolvable (see kiosk implementation
       rule §1.2). Click outside the panel closes. -->
  <div class="kiosk-qr-backdrop" @click.self="$emit('close')">
    <div class="kiosk-qr-panel" role="dialog" aria-modal="true" :aria-label="displayTitle">
      <h2 class="kiosk-qr-title">{{ displayTitle }}</h2>

      <!-- The QR itself stays dark-on-light so phone cameras read it; the app's
           blue accent theming goes AROUND the card, never inverting the modules. -->
      <div class="kiosk-qr-card">
        <qrcode-vue
          :value="url"
          :size="232"
          level="M"
          :margin="1"
          background="#fdf8ef"
          foreground="#140d02"
        />
      </div>

      <p class="kiosk-qr-url">{{ truncatedUrl }}</p>
      <p class="kiosk-qr-caption">Scan with your phone's camera</p>

      <button type="button" class="kiosk-qr-close control-btn-like" @click="$emit('close')">
        Close
      </button>
    </div>
  </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";
import QrcodeVue from "qrcode.vue";

// Middle-truncate the shown URL past this length (the QR encodes the full URL
// regardless — this is display only).
const URL_DISPLAY_MAX = 48;

export default defineComponent({
  name: "KioskQrModal",
  components: { "qrcode-vue": QrcodeVue },
  props: {
    url: { type: String, required: true },
    title: { type: String, default: "" },
    autoCloseMs: { type: Number, default: 45000 },
  },
  emits: ["close"],
  data() {
    return {
      autoCloseTimer: 0,
    };
  },
  computed: {
    displayTitle(): string {
      return this.title || "Scan to visit";
    },
    // e.g. "https://webbtele…scope.org/page" — keep the head (so the domain is
    // legible) and the tail (so the path is), drop the middle.
    truncatedUrl(): string {
      const u = this.url;
      if (u.length <= URL_DISPLAY_MAX) { return u; }
      const head = u.slice(0, 30);
      const tail = u.slice(-15);
      return `${head}…${tail}`;
    },
  },
  mounted() {
    if (this.autoCloseMs > 0) {
      this.autoCloseTimer = window.setTimeout(() => this.$emit("close"), this.autoCloseMs);
    }
  },
  beforeUnmount() {
    window.clearTimeout(this.autoCloseTimer);
  },
});
</script>

<style scoped>
.kiosk-qr-backdrop {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
}

.kiosk-qr-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  max-width: min(92vw, 22rem);
  padding: 1.4rem 1.6rem 1.5rem;
  background: rgba(4, 6, 24, 0.94);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  border: 1px solid var(--accent-color);
  border-radius: 14px;
  box-shadow: 0 0 24px rgba(0, 0, 0, 0.6);
}

.kiosk-qr-title {
  margin: 0;
  color: var(--accent-color);
  font-size: 1.15rem;
  font-weight: 700;
  text-align: center;
}

.kiosk-qr-card {
  background: #fdf8ef;
  border-radius: 12px;
  padding: 16px;
  line-height: 0;
  border: 1px solid var(--accent-color);
  box-shadow: 0 0 8px var(--accent-color);
}

.kiosk-qr-url {
  margin: 0;
  max-width: 100%;
  color: rgba(255, 255, 255, 0.6);
  font-size: 0.75rem;
  word-break: break-all;
  text-align: center;
}

.kiosk-qr-caption {
  margin: 0;
  color: rgba(255, 255, 255, 0.85);
  font-size: 0.85rem;
  text-align: center;
}

/* Matches the app's chrome look: dark fill, blue accent border/text. */
.control-btn-like {
  margin-top: 0.3rem;
  padding: 0.5rem 1.4rem;
  border-radius: 8px;
  background: rgba(4, 6, 24, 0.82);
  border: 1px solid var(--accent-color);
  color: var(--accent-color);
  cursor: pointer;
  font-size: 0.95rem;
  font-weight: 600;
  transition: box-shadow 150ms ease, color 150ms ease;
}
.control-btn-like:hover {
  color: var(--accent-color2);
  box-shadow: 0 0 8px var(--accent-color);
}
</style>
