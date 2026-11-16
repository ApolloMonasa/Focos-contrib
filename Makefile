.PHONY: all build fmt lint test smoke mm-smoke irq-smoke check clean judges test-test001 reference-replay

TARGET := riscv64gc-unknown-none-elf
HOST_TARGET = $(shell rustc -vV | sed -n 's/^host: //p')

all: build

build:
	cargo build --locked --release --target $(TARGET)

fmt:
	cargo fmt --all -- --check

lint:
	cargo clippy --locked --release --target $(TARGET) -- -D warnings
	cargo clippy --locked -p focos-kernel --lib --target $(HOST_TARGET) -- -D warnings

test:
	cargo test --locked -p focos-kernel --lib --target $(HOST_TARGET)
	python3 -B -m unittest discover -s scripts -p 'test_*.py'
	$(MAKE) test-test001

smoke: build
	python3 scripts/smoke.py

mm-smoke: build
	python3 scripts/smoke.py --mm

irq-smoke: build
	python3 scripts/smoke.py --irq

check: fmt lint test smoke

clean:
	cargo clean

# 官方 judge 缓存按文件哈希校验；只有缓存缺失时访问 GitHub。
judges:
	python3 -B scripts/fetch_judges.py

test-test001: judges
	python3 -B scripts/sync_test_plan.py --check
	python3 -B -m unittest discover -s scripts/tests -p 'test_*.py' -v

reference-replay: judges
	python3 -B scripts/sync_test_plan.py --check
	python3 -B scripts/ref/replay_evidence.py --out target/evidence/test001/replay
