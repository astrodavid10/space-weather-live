<template>
  <!-- Anchor IS the tap target (not a wrapper around a link) so the whole
       44px+ circle is clickable, not just the glyph inside it. -->
  <a
    class="brand-mark"
    href="https://www.rocketcenter.com/INTUITIVEPlanetarium"
    target="_blank"
    rel="noopener"
    aria-label="INTUITIVE Planetarium at the U.S. Space &amp; Rocket Center"
    :style="rootStyle"
  >
    <img
      class="brand-mark-img"
      src="../assets/ip-wordmark-white.svg"
      alt=""
      draggable="false"
    >
  </a>
</template>

<script lang="ts">
import { defineComponent, type PropType } from "vue";

/**
 * Corner branding mark — links out to the INTUITIVE Planetarium site.
 *
 * Deliberately layout-agnostic: no `position: fixed` here, because this
 * component doesn't know what else shares the corner (kiosk chrome, safe-area
 * insets, a future landscape layout, ...). It renders `position: absolute`
 * and a parent places it by wrapping it or passing a `class`/`style` that
 * sets top/right/etc — Vue merges those onto the root <a> by default (no
 * `inheritAttrs: false` here), so `<BrandMark class="corner-tl" />` just works.
 *
 * `pointer-events: auto` is set explicitly because the expected home for this
 * mark is inside a `pointer-events: none` chrome-overlay layer (see sol.vue),
 * the same pattern SpacecraftLabel.vue uses to poke a hole through it.
 */
export default defineComponent({
  name: "BrandMark",

  props: {
    /**
     * Edge length in CSS px. Kept inside the 40-56px band called out for a
     * phone corner overlay; still clamped up to a 44px tap target below that
     * band so a caller can't accidentally shrink it under Apple/WCAG minimums.
     */
    size: {
      type: Number as PropType<number>,
      default: 48,
    },
  },

  computed: {
    rootStyle(): Record<string, string> {
      const px = `${Math.max(this.size, 44)}px`;
      return { width: px, height: px };
    },
  },
});
</script>

<style lang="less" scoped>
.brand-mark {
  position: absolute;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  // 44px is the floor regardless of the `size` prop — see rootStyle().
  min-width: 44px;
  min-height: 44px;
  border-radius: 50%;
  pointer-events: auto;
  -webkit-tap-highlight-color: transparent;
  // A faint dark halo behind the glyph. The wordmark is solid white with a
  // transparent background, so over the bright disk (also white/yellow) it
  // would otherwise vanish — this reads as "corner chrome" over both the
  // starfield and the Sun without drawing a hard chip outline.
  background: radial-gradient(circle, rgba(0, 0, 0, 0.4) 0%, rgba(0, 0, 0, 0) 70%);
  transition: opacity 160ms ease;

  &:hover,
  &:focus-visible {
    opacity: 0.85;
  }

  &:focus-visible {
    outline: 2px solid var(--sol-select);
    outline-offset: 2px;
  }
}

.brand-mark-img {
  width: 68%;
  height: 68%;
  // Two stacked drop-shadows fake a soft dark outline around the silhouette
  // (a plain box-shadow can't follow the SVG's cut-out shape the way
  // `filter: drop-shadow` does) — the image analogue of the text-shadow trick
  // SpacecraftLabel.vue uses to keep its labels readable over the disk.
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.9)) drop-shadow(0 0 5px rgba(0, 0, 0, 0.65));
  user-select: none;
}
</style>
