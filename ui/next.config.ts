import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root to this repo (a stray lockfile in a parent dir would
  // otherwise be inferred as the root).
  turbopack: {
    root: path.resolve(__dirname),
  },
  // Build output directory, overridable so a **second** dev server can run against this same
  // source tree. Two `next dev` processes sharing `.next` contend over it and the second dies
  // on startup — which is what stopped a UI change from being verified in a browser while the
  // primary dev server was in use. With this:
  //
  //     NEXT_DIST_DIR=.next-verify npm run dev -- --port 3100
  //
  // a throwaway instance runs beside the one you are already using. Defaults to `.next`, so
  // nothing changes unless the variable is set.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
