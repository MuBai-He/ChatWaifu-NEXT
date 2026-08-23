import { fileURLToPath } from "node:url";
import path from "node:path";

const configDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(configDirectory, "../..");

export default {
  root: repositoryRoot,
  publicDir: false,
  resolve: {
    alias: {
      "@framework": path.resolve(
        repositoryRoot,
        "vendor/live2d/CubismWebFramework/src",
      ),
      "@cubismsdksamples": path.resolve(
        repositoryRoot,
        "vendor/live2d/CubismWebSamples/src",
      ),
    },
  },
  build: {
    target: "es2022",
    minify: false,
    sourcemap: true,
    emptyOutDir: false,
    outDir: path.resolve(repositoryRoot, "apps/web/public/vendor/live2d"),
    lib: {
      entry: path.resolve(configDirectory, "src/index.ts"),
      formats: ["es"],
      fileName: () => "chatwaifu-live2d-bridge.js",
    },
  },
};
