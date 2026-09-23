# Releasing ccx

The release workflow publishes native router binaries and desktop bundles from an
existing `v*` tag. It does not create or move tags.

## Before tagging

All three public component versions must match the tag exactly:

- `CCX_VERSION` in `bin/ccx`
- `[package].version` in `router/Cargo.toml` (and `router/Cargo.lock`)
- `version` in `gui/src-tauri/tauri.conf.json`

Validate the tree with:

```bash
scripts/check-release-version.sh v0.1.0
bash tests/test_ccx.sh
scripts/test-installer.sh
cargo test --locked --manifest-path router/Cargo.toml
cargo test --locked --manifest-path gui/Cargo.toml
```

Only push the tag after CI is green. A tag such as `v0.1.0` triggers
`.github/workflows/release.yml`; workflow dispatch can publish an existing tag
that does not already have a GitHub release.

Router and desktop matrix jobs only produce private workflow artifacts. After all
matrix entries succeed, the final job validates the complete asset set, uploads it
to a hidden draft, and publishes that draft in one final API operation. A build or
upload failure therefore cannot leave a public partial release. An interrupted
final job can leave a non-public draft; inspect and delete that stale draft before
retrying the workflow for the same tag. The workflow intentionally refuses to
modify an existing release.

## Router artifacts

The workflow builds these raw executables natively:

- `ccx-router-linux-x86_64`
- `ccx-router-linux-aarch64`
- `ccx-router-macos-x86_64`
- `ccx-router-macos-aarch64`
- `ccx-router-windows-x86_64.exe`

It refuses to publish an incomplete set, creates `SHA256SUMS`, and attaches a
GitHub build-provenance attestation for every binary. `install.sh` requires a
matching SHA-256 entry before copying a downloaded router into the binary directory.

For an additional detached signature, configure `CCX_COSIGN_PRIVATE_KEY` and
`COSIGN_PASSWORD` as repository secrets. The release will then also contain
`SHA256SUMS.sig` and `cosign.pub`. Without that secret, artifacts still have their
checksum manifest and GitHub provenance, but no detached Cosign signature; release
notes must not describe them as Cosign-signed.

## Desktop artifacts

The native matrix builds deb/AppImage on Linux, a compressed app bundle plus DMG
on Intel and Apple Silicon macOS, and NSIS/MSI on Windows. Apple signing and
notarization are enabled only when the corresponding repository secrets are
configured. Unsigned bundles can still be produced for testing.

The desktop app currently launches `ccx` from `PATH`. It does not embed `bin/ccx`
or `ccx-router` as a Tauri sidecar, so release notes must tell users to install the
CLI before using **Launch**, **Doctor**, or **Certify**.

## Installer contract

`CCX_ROUTER_INSTALL` accepts `auto`, `download`, `build`, or `skip`. The default
tries the release matching the CLI version, then falls back to a locked local Cargo
build. Mirrors and tests can override:

- `CCX_RELEASE_REPOSITORY`
- `CCX_RELEASE_BASE_URL`
- `CCX_RELEASE_VERSION`
- `CCX_BIN_DIR`
- `CCX_ROUTER_BUILD_DIR`

Run `scripts/test-installer.sh` after changing any asset name or download behavior;
it covers successful verification, tamper rejection, and the source-build fallback.
