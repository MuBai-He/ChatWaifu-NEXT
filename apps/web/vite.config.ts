import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import path from "node:path";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

type ProductMode = "web" | "desktop";

interface ReleaseProduct {
  version: string;
  frontend_mode: ProductMode;
  frontend_output: string;
}

interface ReleaseProducts {
  products: Record<ProductMode, ReleaseProduct>;
}

const projectRoot = path.resolve(import.meta.dirname, "../..");
const releaseProducts = JSON.parse(
  readFileSync(path.join(projectRoot, "release/products.json"), "utf8"),
) as ReleaseProducts;

export function resolveProductMode(mode: string): ProductMode {
  return mode === "desktop" ? "desktop" : "web";
}

function productEntryPlugin(product: ProductMode): Plugin {
  const entry = `/src/main.${product}.tsx`;
  const title =
    product === "desktop" ? "ChatWaifu NEXT · 桌宠" : "ChatWaifu NEXT";
  const description =
    product === "desktop"
      ? "ChatWaifu NEXT native desktop companion surface"
      : "ChatWaifu NEXT browser character conversation";
  return {
    name: "chatwaifu-product-entry",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        return html
          .replace("/src/main.product.tsx", entry)
          .replace("<title>ChatWaifu NEXT</title>", `<title>${title}</title>`)
          .replace(
            'content="ChatWaifu NEXT Phase 0 and Phase 1 engineering status"',
            `content="${description}"`,
          );
      },
    },
  };
}

function productManifestPlugin(product: ProductMode): Plugin {
  return {
    name: "chatwaifu-product-manifest",
    generateBundle(_options, bundle) {
      const sourceRoot = `${path.resolve(import.meta.dirname, "src")}${path.sep}`;
      const bundledModules: string[] = [];
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") {
          bundledModules.push(...Object.keys(output.modules));
        }
      }
      const modules = bundledModules
        .filter((moduleId) => moduleId.startsWith(sourceRoot))
        .map((moduleId) =>
          path
            .relative(import.meta.dirname, moduleId)
            .replaceAll(path.sep, "/"),
        );
      this.emitFile({
        type: "asset",
        fileName: "chatwaifu-product.json",
        source: `${JSON.stringify(
          {
            schema_version: "1.0",
            product,
            version: releaseProducts.products[product].version,
            surfaces:
              product === "web"
                ? ["application", "avatar-lab"]
                : ["desktop-pet", "desktop-settings"],
            modules: [...new Set(modules)].sort(),
          },
          null,
          2,
        )}\n`,
      });
    },
  };
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const product = resolveProductMode(mode);
  const releaseProduct = releaseProducts.products[product];
  if (releaseProduct.frontend_mode !== product) {
    throw new Error(
      `release profile for ${product} has a mismatched frontend mode`,
    );
  }
  return {
    plugins: [
      react(),
      productEntryPlugin(product),
      productManifestPlugin(product),
    ],
    build: {
      outDir: path.resolve(projectRoot, releaseProduct.frontend_output),
      emptyOutDir: true,
    },
    test: {
      environment: "jsdom",
      include: [
        "src/**/*.test.{ts,tsx}",
        path.resolve(
          import.meta.dirname,
          "../../tools/live2d_bridge/src/**/*.test.ts",
        ),
      ],
    },
  };
});
