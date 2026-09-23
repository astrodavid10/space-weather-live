// =====================================================================
// "Take it with you" — the URL behind the kiosk QR pill
// =====================================================================
// This is the Dome-to-Phone handoff: a guest points their camera at the pill
// and their phone opens the app showing EXACTLY the channel and view they were
// looking at on the lobby screen. Three things follow from "it has to work on
// a phone, off the museum's network, weeks later":
//
//   1. The base URL is `kioskHomeUrl` (set in main.ts) whenever it's set. The
//      exhibit machine's own address is usually localhost or a LAN IP, which
//      no guest's phone can reach; deriving from window.location is only a
//      development fallback.
//   2. The kiosk-only params never travel. A guest's phone must not boot into
//      kiosk mode, and ?kioskIdle / ?kioskStats / ?debug mean nothing off the
//      exhibit floor.
//   3. The deep-link params are stripped and then re-added from live state, so
//      the QR encodes what is on screen RIGHT NOW — not whatever the URL says
//      (useDeepLink writes it back on a 300 ms debounce, and during the attract
//      loop it reflects the loop, not the guest).

import { urlWithoutParams } from "../urlParams";

/** Dropped from the derived base: kiosk flags, plus everything we re-add. */
const STRIPPED_PARAMS = [
  "kiosk", "kioskIdle", "kioskStats", "debug",
  "src", "t", "cam", "layers", "style", "q", "texch", "surface", "fieldcolor",
  // Legacy disk-view params. The app no longer writes them, but a kiosk URL
  // configured before the single-view consolidation may still carry them, and
  // they must not ride along into a guest's take-home link.
  "view", "wl", "pfss", "movie", "res",
];

/** Development fallback: this page's origin + path, kiosk params removed. */
export function derivedHomeUrl(): string {
  const url = urlWithoutParams(...STRIPPED_PARAMS);
  // urlWithoutParams keeps the fragment, and a query string cannot follow one.
  const hash = url.indexOf("#");
  return hash < 0 ? url : url.slice(0, hash);
}

/**
 * `<base>?texch=<channel>` — the deep link useDeepLink reads on the other end.
 * Pure apart from the `base === ""` fallback, so it can be exercised without a
 * browser.
 */
export function takeHomeUrl(base: string, params: Record<string, string>): string {
  const root = base || derivedHomeUrl();
  const q = Object.entries(params).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
  return q.length ? root + (root.includes("?") ? "&" : "?") + q.join("&") : root;
}
