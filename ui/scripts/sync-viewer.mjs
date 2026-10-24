// Copy the ossie-viewer build into public/viewer, with the commit it was built from (ADR 0021).
// Run after `npm run build` in ../ossie-viewer:  npm run sync-viewer
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const viewer = path.resolve(ui, process.argv[2] ?? "../../ossie-viewer");
const built = path.join(viewer, "dist", "index.html");
if (!existsSync(built)) {
  console.error(`No build at ${built}. Run npm run build in ${viewer} first.`);
  process.exit(1);
}
const commit = execFileSync("git", ["-C", viewer, "rev-parse", "--short", "HEAD"], { encoding: "utf8" }).trim();
const dirty = execFileSync("git", ["-C", viewer, "status", "--porcelain"], { encoding: "utf8" }).trim();
if (dirty) {
  console.error(`${viewer} has uncommitted changes, so no commit names this build.`);
  process.exit(1);
}
const target = path.join(ui, "public", "viewer");
mkdirSync(target, { recursive: true });
copyFileSync(built, path.join(target, "index.html"));
writeFileSync(path.join(target, "COMMIT"), `${commit}\n`);
console.log(`public/viewer/index.html from ossie-viewer ${commit}`);
