// Kiosk-mode runtime helpers — app-agnostic. Copy this file verbatim into a
// sibling data-story repo to add museum-touchscreen behavior (see KIOSK_MODE.md).
//
// Three concerns:
//   • createIdleWatcher   — detect "nobody's touched it" → attract loop.
//   • installKioskGuards  — intercept every external link / navigation; lock the
//                           browser down (no context menu, no pinch-zoom of the
//                           page, no pull-to-refresh).
//   • scheduleDailyReload — a nightly reload during attract to shed WWT's tile
//                           cache over multi-day uptimes.
//
// The URL query flag lives elsewhere (urlParams.ts); the caller passes plain
// callbacks so this file has zero app knowledge.

// ── Tunables ────────────────────────────────────────────────────────────────
export const KIOSK_IDLE_MS = 90_000;                  // idle → attract
export const KIOSK_ATTRACT_DWELL_MS = 12_000;         // pause on each image after slew
export const KIOSK_ATTRACT_FALLBACK_STEP_MS = 30_000; // cadence if no slew duration known
export const KIOSK_QR_AUTOCLOSE_MS = 45_000;          // QR modal self-dismiss
export const KIOSK_RELOAD_HOUR = 3;                   // nightly maintenance reload (3 AM)

// Attract-loop "cosmic zoom-out" 3D interlude: every Nth attract step detours
// through 3D to show where the current + next images actually sit in space.
export const KIOSK_ATTRACT_3D_EVERY = 4;              // every Nth attract step gets the interlude; 0 disables
export const KIOSK_ATTRACT_3D_HOLD_MS = 4000;         // hold at the current image's marker before pulling back
export const KIOSK_ATTRACT_3D_FLYOUT_S = 6;           // seconds — gotoTargetFullHacked duration, pull-back to overview
export const KIOSK_ATTRACT_3D_OVERVIEW_DWELL_MS = 7000; // dwell at the Milky Way overview before flying to the next image
export const KIOSK_ATTRACT_3D_NEXT_HOLD_MS = 5000;    // hold at the next image's marker before dropping back to 2D

// Only ever surface real web links as QR codes (never about:blank, javascript:, …).
function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

// ── Idle watcher ──────────────────────────────────────────────────────────
export interface IdleWatcherOptions {
  idleMs: number;
  onIdle: () => void;
  onActive: () => void;
  /** Fired only for a discrete pointerdown (a "tap"); used for stats. */
  onTap?: () => void;
}

export interface IdleWatcher {
  start(): void;
  stop(): void;
  lastActivityTs(): number;
}

export function createIdleWatcher(opts: IdleWatcherOptions): IdleWatcher {
  let lastActivity = Date.now();
  let idle = false;
  let intervalId = 0;
  let lastMoveWrite = 0;
  let running = false;

  const markActivity = (): void => {
    lastActivity = Date.now();
    idle = false;
    opts.onActive();
  };

  const onPointerDown = (): void => { markActivity(); opts.onTap?.(); };
  const onKeyDown = (): void => markActivity();
  const onWheel = (): void => markActivity();
  const onTouchStart = (): void => markActivity();
  // Museum touchscreens fire streams of pointermove; only bump the timestamp,
  // throttled, and never treat it as a discrete "activity" (no onActive/onTap).
  const onPointerMove = (): void => {
    const now = Date.now();
    if (now - lastMoveWrite >= 1000) {
      lastMoveWrite = now;
      lastActivity = now;
      idle = false;
    }
  };

  // Capture phase so overlay UI that stops propagation can't hide activity.
  const capture = true;
  const add = (): void => {
    window.addEventListener("pointerdown", onPointerDown, capture);
    window.addEventListener("keydown", onKeyDown, capture);
    window.addEventListener("wheel", onWheel, capture);
    window.addEventListener("touchstart", onTouchStart, capture);
    window.addEventListener("pointermove", onPointerMove, capture);
  };
  const remove = (): void => {
    window.removeEventListener("pointerdown", onPointerDown, capture);
    window.removeEventListener("keydown", onKeyDown, capture);
    window.removeEventListener("wheel", onWheel, capture);
    window.removeEventListener("touchstart", onTouchStart, capture);
    window.removeEventListener("pointermove", onPointerMove, capture);
  };

  return {
    start(): void {
      if (running) { return; }
      running = true;
      lastActivity = Date.now();
      idle = false;
      add();
      intervalId = window.setInterval(() => {
        if (!idle && Date.now() - lastActivity > opts.idleMs) {
          idle = true;
          opts.onIdle();
        }
      }, 5000);
    },
    stop(): void {
      if (!running) { return; }
      running = false;
      remove();
      window.clearInterval(intervalId);
      intervalId = 0;
    },
    lastActivityTs(): number { return lastActivity; },
  };
}

// ── Browser lockdown guards ─────────────────────────────────────────────────
export interface KioskGuardOptions {
  /** Called for every intercepted external link / window.open (http/https only). */
  onExternalLink: (url: string, title: string) => void;
}

// Best-effort human label for an intercepted anchor.
function anchorTitle(a: HTMLAnchorElement, href: string): string {
  const aria = a.getAttribute("aria-label");
  if (aria) { return aria.trim(); }
  const titleAttr = a.getAttribute("title");
  if (titleAttr) { return titleAttr.trim(); }
  const img = a.querySelector("img[alt]");
  const alt = img?.getAttribute("alt");
  if (alt) { return alt.trim(); }
  const text = (a.textContent ?? "").trim();
  if (text) { return text; }
  try { return new URL(href).hostname; } catch { return ""; }
}

export function installKioskGuards(opts: KioskGuardOptions): () => void {
  // (a) Capture-phase click interceptor: covers every static external anchor
  // (including per-image "Learn more") without touching individual markup.
  const onClick = (ev: MouseEvent): void => {
    const target = ev.target as Element | null;
    const a = target?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a) { return; }
    const href = a.href; // resolved absolute URL
    let offOrigin = false;
    try { offOrigin = new URL(href).origin !== window.location.origin; } catch { offOrigin = false; }
    const blankTarget = a.target === "_blank";
    if (!offOrigin && !blankTarget) { return; }
    ev.preventDefault();
    ev.stopPropagation();
    if (isHttpUrl(href)) { opts.onExternalLink(href, anchorTitle(a, href)); }
  };
  document.addEventListener("click", onClick, true);

  // (b) Belt-and-braces: no programmatic window.open ever pops a tab in kiosk
  // mode. Real web URLs become a QR; everything else (about:blank, etc.) is
  // silently suppressed.
  const origOpen = window.open;
  window.open = ((url?: string | URL): Window | null => {
    const u = String(url ?? "");
    if (isHttpUrl(u)) { opts.onExternalLink(u, ""); }
    return null;
  }) as typeof window.open;

  // (c) No native context menu on long-press / right-click.
  const onContextMenu = (ev: Event): void => ev.preventDefault();
  document.addEventListener("contextmenu", onContextMenu);

  // (d) Disable page pinch-zoom (the WWT canvas keeps its own pinch). Done in JS
  // so the normal mobile build keeps pinch-zoom accessibility.
  const viewport = document.querySelector('meta[name="viewport"]') as HTMLMetaElement | null;
  const origViewport = viewport?.getAttribute("content") ?? null;
  if (viewport) {
    const base = origViewport ?? "width=device-width, initial-scale=1";
    viewport.setAttribute("content", `${base}, maximum-scale=1, user-scalable=no`);
  }

  // (e) Body hook for CSS (pull-to-refresh, callout).
  document.body.classList.add("kiosk-mode");

  return function cleanup(): void {
    document.removeEventListener("click", onClick, true);
    window.open = origOpen;
    document.removeEventListener("contextmenu", onContextMenu);
    if (viewport && origViewport !== null) { viewport.setAttribute("content", origViewport); }
    document.body.classList.remove("kiosk-mode");
  };
}

// ── Nightly maintenance reload ──────────────────────────────────────────────
// Once per day at `hour`, when it's safe (i.e. during attract, so no guest sees
// it), reload to shed WWT's growing tile cache. The query string survives a
// reload, so ?kiosk=1 persists. Returns a cancel function.
export function scheduleDailyReload(hour: number, isSafeToReload: () => boolean): () => void {
  let firedForDay = -1;
  const intervalId = window.setInterval(() => {
    const now = new Date();
    const dayKey = now.getFullYear() * 1000 + (now.getMonth() * 31 + now.getDate());
    if (now.getHours() === hour && firedForDay !== dayKey && isSafeToReload()) {
      firedForDay = dayKey;
      window.location.reload();
    }
  }, 60_000);
  return function cancel(): void { window.clearInterval(intervalId); };
}
