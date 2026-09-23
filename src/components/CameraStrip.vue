<template>
  <div class="camera-strip" data-camera-passthrough="false" role="radiogroup" aria-label="Camera">
    <button
      v-for="p in presets"
      :key="p.id"
      type="button"
      role="radio"
      class="cs-chip"
      :class="{ 'is-on': camera === p.id }"
      :aria-checked="camera === p.id ? 'true' : 'false'"
      @click="pick(p.id)"
    >
      <font-awesome-icon v-if="p.id === 'home'" icon="house" />
      <span>{{ p.label }}</span>
    </button>
  </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";

import { CameraId, camera, goCamera } from "../state/useAppState";

// Mirrors wwt/magnetoStage.ts CAMERA_PRESETS; duplicated here because this
// component lives in the entry chunk, which must not import the engine.
const PRESETS: { id: CameraId; label: string }[] = [
  { id: "home", label: "Home" },
  { id: "wide", label: "Wide" },
  { id: "top", label: "Top-down" },
  { id: "headon", label: "Head-on" },
  { id: "tail", label: "Down the tail" },
  { id: "dawn", label: "Dawn flank" },
  { id: "dusk", label: "Dusk flank" },
  { id: "ncusp", label: "N cusp" },
  { id: "scusp", label: "S cusp" },
];

export default defineComponent({
  name: "CameraStrip",
  setup() { return { camera, presets: PRESETS }; },
  methods: { pick(id: CameraId): void { goCamera(id); } },
});
</script>

<style lang="less" scoped>
.camera-strip {
  display: flex;
  gap: 0.35rem;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x proximity;
  scrollbar-width: none;
  touch-action: pan-x;
  padding: 0.1rem 0.1rem 0.2rem;
  -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
  mask-image: linear-gradient(90deg, transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
  &::-webkit-scrollbar { display: none; }
}

.cs-chip {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-height: 40px;
  min-width: 60px;
  padding: 0 0.75rem;
  scroll-snap-align: start;
  border: 1px solid var(--sol-hairline);
  border-radius: 999px;
  background: rgba(8, 12, 34, 0.88);
  color: var(--sol-text-dim);
  font-size: 0.74rem;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
  &.is-on { border-color: rgba(var(--sol-select-rgb), 0.75); background: rgba(32, 34, 60, 0.95); color: var(--sol-text); }
}
</style>
