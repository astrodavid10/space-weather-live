// Query-string helpers. Shared foundation: kiosk mode today, P3.1 deep links later.

export function queryParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

/** True when the param is present and not explicitly "0" or "false". */
export function boolParam(name: string): boolean {
  const v = queryParams().get(name);
  return v !== null && v !== "0" && v !== "false";
}

/**
 * True unless the param is present AND explicitly "0" or "false".
 *
 * The mirror of boolParam, for a feature that is ON by default and needs an
 * opt-OUT. boolParam cannot express this: "absent" and "?x=0" both read as
 * false there, so a default-on flag written with it can never be turned off.
 */
export function boolParamDefaultTrue(name: string): boolean {
  const v = queryParams().get(name);
  return v === null || (v !== "0" && v !== "false");
}

export function stringParam(name: string): string | null {
  return queryParams().get(name);
}

export function numberParam(name: string): number | null {
  const v = queryParams().get(name);
  if (v === null) { return null; }
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Current URL (origin + path + hash) with the named query params removed. */
export function urlWithoutParams(...names: string[]): string {
  const params = queryParams();
  names.forEach((n) => params.delete(n));
  const qs = params.toString();
  return window.location.origin + window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
}
