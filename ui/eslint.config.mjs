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
    // The throwaway dev-server build dirs (NEXT_DIST_DIR, see .gitignore). They are
    // bundled Turbopack output — `require()` calls, `@ts-ignore`s, assignments to
    // `module` — so `npx eslint .` reported hundreds of errors from generated code
    // and nobody could run the repo-wide gate. Ignored for the same reason
    // `.next/**` is; they were only missed because they are further dist dirs.
    // Globbed rather than listed: `.next-verify/**` was named alone until
    // 2026-08-22, and `.next-mock/**` — created by `.claude/launch.json`'s
    // `ui-mock-3200` — then contributed 547 errors on its own.
    ".next-*/**",
  ]),
]);

export default eslintConfig;
