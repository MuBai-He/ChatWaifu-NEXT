# Web and Desktop product profiles

ChatWaifu NEXT uses one repository and one `main` branch, but produces two independently versioned
frontend graphs.

| Product | Owned surfaces | Build command | Frontend artifact | Tag |
| --- | --- | --- | --- | --- |
| Web | Galgame conversation, Avatar Lab | `make build-web` | `apps/web/dist/web` | `web-vX.Y.Z` |
| Desktop | Desktop pet, control center | `make build-desktop-ui` | `apps/web/dist/desktop` | `desktop-vX.Y.Z` |

Both artifacts contain shared conversation, Runtime-client, Live2D, voice, memory, and settings
modules only when their owned surface needs them. `chatwaifu-product.json` records the actual source
modules in each bundle. Verify it after a build:

```bash
uv run python tools/verify_product_artifacts.py --product web
uv run python tools/verify_product_artifacts.py --product desktop
```

The canonical product versions are in `release/products.json`. Update one release train without
changing the other:

```bash
uv run python tools/product_release.py set-version --product web --version 0.2.1
uv run python tools/product_release.py verify --product web --tag web-v0.2.1
```

The updater synchronizes the required package/Tauri/Cargo mirrors. Runtime, protocol, and worker
component versions remain independent.

Tauri development and builds always invoke the Desktop Vite profile and consume
`apps/web/dist/desktop`. The Desktop host currently builds as an unsigned no-bundle executable; a
frozen Runtime sidecar, resources, installer, signing, and target-machine installation smoke remain
hard gates before a `desktop-v*` artifact can be described as an installable release.
