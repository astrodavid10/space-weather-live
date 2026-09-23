// =====================================================================
// Network Information API — a hint, never a gate
// =====================================================================
// The API is Chromium-only, so ABSENCE MEANS "assume a good connection"
// (Safari/Firefox users would otherwise be permanently downgraded to 1024
// stills). Everything here is advisory: the guest can always override by
// tapping the resolution chip or the movie's play button.

interface ConnectionLike {
  effectiveType?: string;
  saveData?: boolean;
}

function connection(): ConnectionLike | undefined {
  return (navigator as Navigator & { connection?: ConnectionLike }).connection;
}

/**
 * True when the browser tells us the guest is on Data Saver or a connection
 * slower than 4g. Used to pick a smaller still and to offer the 12 MB daily
 * movie ahead of the 33 MB one.
 */
export function isMetered(): boolean {
  const info = connection();
  if (!info) { return false; }
  if (info.saveData) { return true; }
  return info.effectiveType !== undefined && info.effectiveType !== "4g";
}

/**
 * True when a big optional download (the 4096 still on deep zoom) is
 * reasonable: reported 4g, or no information at all.
 */
export function allowsHeavyLoad(): boolean {
  const info = connection();
  if (!info) { return true; }
  if (info.saveData) { return false; }
  return info.effectiveType === undefined || info.effectiveType === "4g";
}
