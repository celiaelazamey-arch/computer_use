import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: ["coverage/**", "dist/**", "node_modules/**", ".wrangler/**"],
  },
  js.configs.recommended,
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "no-console": ["error", { allow: ["error", "warn"] }],
    },
  },
];
