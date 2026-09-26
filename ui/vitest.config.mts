import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// No `@vitejs/plugin-react`: its current release peers on a Babel 8 prerelease that conflicts
// with shadcn's Babel 7, and Vite's own transform already compiles `react-jsx` TSX.
// `globals` is on so Testing Library registers its automatic `cleanup` after each test.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.test.tsx"],
  },
});
