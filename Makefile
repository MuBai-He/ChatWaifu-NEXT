UV ?= uv
PNPM ?= $(UV) run python tools/run_pnpm.py
CARGO ?= cargo

.PHONY: bootstrap demo desktop build-web build-desktop-ui build-desktop-host dev-docs build-docs preview-docs publish-docs verify-release format format-check lint typecheck generate-protocol check-generated test test-contract test-e2e test-avatar test-runtime setup-nltk-data setup-stt-worker setup-tts-worker setup-neural-tts-workers setup-live2d-framework setup-live2d-vendor build-live2d-bridge check-live2d-vendor dev-runtime dev-web dev-avatar-lab dev-desktop clean

bootstrap:
	$(UV) sync --all-packages --all-groups
	$(MAKE) setup-nltk-data
	$(PNPM) install --frozen-lockfile
	$(CARGO) fetch --locked
	$(MAKE) generate-protocol

demo:
	$(UV) run python tools/run_demo.py $(DEMO_ARGS)

desktop: bootstrap setup-stt-worker setup-neural-tts-workers
	$(PNPM) --filter @chatwaifu/desktop dev

build-web:
	$(PNPM) --filter @chatwaifu/web build:web

build-desktop-ui:
	$(PNPM) --filter @chatwaifu/web build:desktop

build-desktop-host:
	$(PNPM) --filter @chatwaifu/desktop build

dev-docs:
	$(PNPM) docs:dev

build-docs:
	$(PNPM) docs:build

preview-docs:
	$(PNPM) docs:preview

publish-docs: build-docs
	$(UV) run python tools/publish_docs_site.py --publish

verify-release:
	$(UV) run python tools/product_release.py verify --product $(PRODUCT) --tag $(TAG)

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

setup-nltk-data:
	$(UV) run python tools/setup_nltk_data.py

setup-stt-worker:
	$(UV) run python tools/setup_stt_worker.py

setup-tts-worker:
	$(UV) run python tools/setup_tts_worker.py

setup-neural-tts-workers:
	$(UV) run python tools/setup_neural_tts_workers.py

setup-live2d-framework:
	bash tools/setup_live2d_framework.sh

setup-live2d-vendor: setup-live2d-framework
	$(UV) run python tools/setup_live2d_vendor.py $(LIVE2D_SETUP_ARGS)
	$(MAKE) build-live2d-bridge
	$(MAKE) check-live2d-vendor

build-live2d-bridge:
	$(PNPM) --filter @chatwaifu/web exec vite build --config ../../tools/live2d_bridge/vite.config.mts

check-live2d-vendor:
	node tools/check_live2d_vendor.mjs

dev-runtime:
	$(UV) run python tools/run_runtime.py

dev-web:
	$(PNPM) --filter @chatwaifu/web dev

dev-avatar-lab:
	$(PNPM) --filter @chatwaifu/web dev -- --open /avatar-lab

dev-desktop:
	$(PNPM) --filter @chatwaifu/desktop dev

clean:
	rm -rf .venv target apps/web/dist apps/web/node_modules packages/protocol-typescript/dist node_modules
