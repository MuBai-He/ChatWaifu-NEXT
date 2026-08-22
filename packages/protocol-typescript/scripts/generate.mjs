import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { compileFromFile } from "json-schema-to-typescript";

const packageRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const schemaPath = path.resolve(
  packageRoot,
  "../../schemas/domain/v1/protocol-catalog.schema.json",
);
const outputPath = path.resolve(packageRoot, "src/generated/domain.ts");
const source = await compileFromFile(schemaPath, {
  bannerComment:
    "// Generated from chatwaifu-protocol Pydantic models. Run make generate-protocol; do not edit.",
  style: { semi: false, singleQuote: true },
});

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, source, "utf8");
