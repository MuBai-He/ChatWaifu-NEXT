UV ?= uv
PNPM ?= pnpm
CARGO ?= cargo

.PHONY: bootstrap demo format format-check lint typecheck generate-protocol check-generated test test-contract test-e2e test-avatar test-runtime setup-live2d-framework check-live2d-vendor dev-runtime dev-web dev-avatar-lab dev-desktop clean

bootstrap:
	$(UV) sync --all-packages --all-groups
	$(PNPM) install
	$(CARGO) fetch --locked
	$(MAKE) generate-protocol

demo:
	$(UV) run python tools/run_demo.py

format:
	$(UV) run ruff format .
	$(PNPM) format
	$(CARGO) fmt --all

format-check:
	$(UV) run ruff format --check .
	$(PNPM) format:check
	$(CARGO) fmt --all --check

lint:
	$(UV) run ruff check .
	$(PNPM) lint
	$(CARGO) clippy --workspace --all-targets -- -D warnings

typecheck:
	$(UV) run pyright
	$(PNPM) typecheck
	$(CARGO) check --workspace --all-targets

generate-protocol:
	$(PNPM) protocol:generate

check-generated: generate-protocol
	git diff --exit-code -- schemas/domain/v1 packages/protocol-typescript/src/generated tests/fixtures/protocol/v1

test:
	$(UV) run pytest
	$(PNPM) test
	$(CARGO) test --workspace

test-contract:
	$(UV) run pytest packages/protocol-python/tests tests/contract
	$(PNPM) --filter @chatwaifu/protocol test

test-e2e:
	$(PNPM) --filter @chatwaifu/web test:e2e

test-avatar:
	$(PNPM) --filter @chatwaifu/avatar-sdk test
	$(PNPM) --filter @chatwaifu/web test

test-runtime:
	$(UV) run pytest services/runtime/tests

setup-live2d-framework:
	bash tools/setup_live2d_framework.sh

check-live2d-vendor:
	node tools/check_live2d_vendor.mjs

dev-runtime:
	$(UV) run python tools/run_runtime.py

dev-web:
	$(PNPM) --filter @chatwaifu/web dev

dev-avatar-lab:
	$(PNPM) --filter @chatwaifu/web dev -- --open /avatar-lab

dev-desktop:
	@echo "Tauri host starts in Phase 3; Rust workspace checks are available now."

clean:
	rm -rf .venv target apps/web/dist apps/web/node_modules packages/protocol-typescript/dist node_modules
