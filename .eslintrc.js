module.exports = {
  root: true,
  ignorePatterns: [],
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    extraFileExtensions: ['.vue']
  },
  plugins: [
    '@typescript-eslint',
  ],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/eslint-recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:vue/essential',
    '@vue/typescript/recommended'
  ],
  rules: {
    "indent": ["error", 2],
    "@typescript-eslint/naming-convention": [
      "error", {
        selector: ["variable", "memberLike", "function"],
        format: ["camelCase"],
        leadingUnderscore: "allow"
      },
      {
        selector: ["variable"],
        modifiers: ["global", "const"],
        format: ["camelCase", "UPPER_CASE"],
        leadingUnderscore: "allow"
      },
      {
        selector: "typeLike",
        format: ["PascalCase"],
        leadingUnderscore: "allow"
      },
      {
        selector: [
          "classProperty",
          "objectLiteralProperty",
          "typeProperty",
          "classMethod",
          "objectLiteralMethod",
          "typeMethod",
          "accessor",
          "enumMember"
        ],
        format: null,
        modifiers: ["requiresQuotes"]
      }
    ],
    "@typescript-eslint/no-unused-vars": [
      "error", {
        "args": "all",
        "argsIgnorePattern": "^_",
        "varsIgnorePattern": "^_"
      }
    ],
    "@typescript-eslint/semi": "error",
    "vue/multi-word-component-names": "off",
    // Diagnostics (shader link failures, audio decode errors, engine warnings)
    // are worth shipping; console.log is almost always leftover debugging. The
    // deliberate console-driven debug helpers (logLayerData, the
    // window.__pingDebug / window.__gx* knobs) opt back in with a local
    // eslint-disable so the intent is explicit at the call site.
    "no-console": ["error", { allow: ["warn", "error"] }]
  }
};
