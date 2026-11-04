.PHONY: all build fmt lint smoke check clean

TARGET := riscv64gc-unknown-none-elf

all: build

build:
	cargo build --locked --release --target $(TARGET)

fmt:
	cargo fmt --all -- --check

lint:
	cargo clippy --locked --release --target $(TARGET) -- -D warnings

smoke: build
	python3 scripts/smoke.py

check: fmt lint smoke

clean:
	cargo clean
