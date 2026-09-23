// Vendored from cosmicds/three-wwt @ 80b95028d2b1e9ba7dbc117c314b25f535e80847
// (MIT — see LICENSE). See setupThreeWWT.ts / utils.ts headers for the Sol
// modifications and CLAUDE.md footgun 15 for why this is vendored rather than
// installed from npm.

export { setupThreeWWT } from "./setupThreeWWT";
export type { ThreeWWTSetup, UseThreeOptions } from "./setupThreeWWT";
export { WebGL2UnavailableError, wwtMatrixToTHREE, updateTHREECamera } from "./utils";
