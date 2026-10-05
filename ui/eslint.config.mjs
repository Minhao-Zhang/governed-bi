import { fixupConfigRules } from "@eslint/compat";
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  // `fixupConfigRules` because ESLint 10 removed the deprecated `context.getFilename()` and
  // friends, and `eslint-plugin-react` -- pulled in by `eslint-config-next`, still at 7.37.5 with
  // `peerDependencies.eslint` capped at `^9.7`, including on next's 16.4 canary -- still calls
  // them: unshimmed, lint dies loading `react/display-name` (observed on dependabot's eslint 10
  // PR, 2026-10-05). The shim restores those methods per rule. `eslint-plugin-import` and
  // `eslint-plugin-jsx-a11y` cap their peer at `^9` too; they load fine, the shim covers them
  // anyway. Drop it once those plugins ship ESLint 10 support -- `npm ls eslint` reports the
  // invalid peers until then. Same 3 warnings, 0 errors as ESLint 9 on the day of the bump.
  ...fixupConfigRules([...nextVitals, ...nextTs]),
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
