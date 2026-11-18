# altdemo

A small Rust command line tool.

## Installation

If you're a Windows Winget user:

```bash
winget install Demo.altdemo
```

If you're a Windows Scoop user:

```bash
scoop install altdemo
```

With a Rust toolchain:

```bash
cargo install altdemo
```

Or, to download a prebuilt binary:

```bash
cargo binstall altdemo
```

On Debian, use the release package:

```bash
curl -LO https://github.com/demo/altdemo/releases/download/1.0.0/altdemo_1.0.0_amd64.deb
sudo dpkg -i altdemo_1.0.0_amd64.deb
```

## Building

```bash
git clone https://github.com/demo/altdemo
cd altdemo
cargo build --release
```

To build with an optional feature:

```bash
cargo build --release --features x
```

To build a static Linux binary:

```bash
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
```
