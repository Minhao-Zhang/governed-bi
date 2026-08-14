import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // The throwaway dev-server build dir (NEXT_DIST_DIR, see .gitignore). It is
    // bundled Turbopack output — `require()` calls, `@ts-ignore`s, assignments to
    // `module` — so `npx eslint .` reported hundreds of errors from generated code
    // and nobody could run the repo-wide gate. Ignored for the same reason
    // `.next/**` is; it was only missed because it is a second dist dir.
    ".next-verify/**",
  ]),
]);

export default eslintConfig;
