set shell := ["sh", "-cu"]

bootstrap:
    make bootstrap

demo:
    make demo

format:
    make format

lint:
    make lint

typecheck:
    make typecheck

generate-protocol:
    make generate-protocol

test:
    make test

test-contract:
    make test-contract

test-e2e:
    make test-e2e

test-avatar:
    make test-avatar

test-runtime:
    make test-runtime

setup-live2d-framework:
    make setup-live2d-framework

check-live2d-vendor:
    make check-live2d-vendor

dev-runtime:
    make dev-runtime

dev-web:
    make dev-web

dev-avatar-lab:
    make dev-avatar-lab

dev-desktop:
    make dev-desktop

clean:
    make clean
