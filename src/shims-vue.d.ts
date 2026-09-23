// Wildcard module declaration for single-file components.
//
// Lives in src/ deliberately: tsconfig.json's `include` covers only
// "plugins/*.ts", "src/**/*.ts" and "src/**/*.vue", so the repo-root
// shims-vue.d.ts is invisible to the compiler (and its contents are commented
// out besides). Without this, every `import X from "./Foo.vue"` is a TS2307
// error, which made `tsc --noEmit` useless as a gate and left editor
// IntelliSense blind to SFC imports. webpack/vue-loader resolves .vue at build
// time regardless, so this affects type-checking only.

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/ban-types
  const component: DefineComponent<{}, {}, any>;
  export default component;
}
