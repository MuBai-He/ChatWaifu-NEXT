import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(
  await readFile(path.join(root, "vendor/live2d/manifest.json"), "utf8"),
);
const checks = [
  [
    "official Framework",
    "vendor/live2d/CubismWebFramework/src/live2dcubismframework.ts",
  ],
  ["Cubism Core", "apps/web/public/vendor/live2d/live2dcubismcore.min.js"],
  [
    "ChatWaifu Cubism bridge",
    "apps/web/public/vendor/live2d/chatwaifu-live2d-bridge.js",
  ],
  ["licensed model", "apps/web/public/vendor/live2d/model/avatar.model3.json"],
];

let missing = 0;
for (const [label, relativePath] of checks) {
  try {
    await access(path.join(root, relativePath));
    console.log(`ready: ${label} (${relativePath})`);
  } catch {
    missing += 1;
    console.error(`missing: ${label} (${relativePath})`);
  }
}

console.log(
  `expected Framework: ${manifest.framework.release}, tag ${manifest.framework.tag}`,
);
if (missing) {
  console.error(
    `Live2D vendor setup is incomplete (${missing} item(s) missing). See vendor/live2d/README.md.`,
  );
  process.exitCode = 1;
}
