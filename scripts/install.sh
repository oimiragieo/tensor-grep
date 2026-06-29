#!/usr/bin/env bash

set -e

ORIGINAL_DIR="$(pwd)"
INSTALL_CHANNEL="${TENSOR_GREP_CHANNEL:-stable}"
REQUESTED_VERSION="${TENSOR_GREP_VERSION:-}"

restore_original_dir() {
    if [ -n "${STAGING_INSTALL_DIR:-}" ] && [ -d "$STAGING_INSTALL_DIR" ]; then
        rm -rf "$STAGING_INSTALL_DIR"
    fi
    cd "$ORIGINAL_DIR" || return
    echo "Returned to original directory: $ORIGINAL_DIR"
}

trap restore_original_dir EXIT

clear_tensor_grep_uv_cache() {
    if [ "$INSTALL_CHANNEL" != "stable" ]; then
        return
    fi
    echo "      Clearing cached tensor-grep package metadata for stable install..."
    if ! uv cache clean tensor-grep; then
        echo "      Unable to clear cached tensor-grep package metadata; continuing with fresh install attempt." >&2
    fi
}

native_frontdoor_asset_candidates() {
    local requested_flavor="${TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR:-${TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR:-cpu}}"
    requested_flavor="$(printf '%s' "$requested_flavor" | tr '[:upper:]' '[:lower:]')"
    case "$requested_flavor" in
        nvidia|cuda)
            printf '%s\n' nvidia cpu
            ;;
        cpu)
            printf '%s\n' cpu
            ;;
        *)
            echo "      Unknown native front-door asset flavor '$requested_flavor'; using CPU asset." >&2
            printf '%s\n' cpu
            ;;
    esac
}

restore_previous_install() {
    if [ ! -d "$BACKUP_INSTALL_DIR" ]; then
        return
    fi
    rm -rf "$INSTALL_DIR"
    mv "$BACKUP_INSTALL_DIR" "$INSTALL_DIR"
}

# Verify a downloaded release asset against the published CHECKSUMS.txt manifest BEFORE
# it is ever made executable or run. Without this, a compromised release/account or a
# TLS-intercepting proxy could persist arbitrary code as the default `tg` (audit S4).
_compute_sha256_hex() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" 2>/dev/null | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
    fi
    return 0
}

_expected_asset_sha256() {
    # $1 = CHECKSUMS.txt path, $2 = asset name. Lines are "<sha256>  <asset>".
    [ -f "$1" ] || return 0
    awk -v asset="$2" '$NF == asset { print $1; exit }' "$1"
    return 0
}

_verify_native_asset() {
    # $1 = downloaded file, $2 = CHECKSUMS.txt path, $3 = asset name. 0 = verified.
    local expected actual
    expected="$(_expected_asset_sha256 "$2" "$3")"
    actual="$(_compute_sha256_hex "$1")"
    if [ -z "$expected" ]; then
        echo "      No published checksum for $3; refusing to trust the download." >&2
        return 1
    fi
    if [ -z "$actual" ]; then
        echo "      Cannot compute sha256 (need sha256sum or shasum); refusing the download." >&2
        return 1
    fi
    if [ "$expected" != "$actual" ]; then
        echo "      Checksum MISMATCH for $3 (expected $expected, got $actual); refusing the download." >&2
        return 1
    fi
    return 0
}

commit_staged_install() {
    rm -rf "$BACKUP_INSTALL_DIR"
    if [ -d "$INSTALL_DIR" ]; then
        mv "$INSTALL_DIR" "$BACKUP_INSTALL_DIR"
    fi
    if mv "$STAGING_INSTALL_DIR" "$INSTALL_DIR"; then
        rm -rf "$BACKUP_INSTALL_DIR"
    else
        restore_previous_install
        return 1
    fi
}

echo "=========================================================="
echo "           TENSOR-GREP LINUX/MACOS INSTALLER              "
echo "=========================================================="

# 1. Install or locate uv
# Pin uv to an exact version for reproducible, supply-chain-safe installs: the versioned astral
# installer downloads that exact uv release and verifies its checksum. Bump deliberately.
# NOTE: On Linux/macOS the versioned astral installer self-verifies the downloaded uv binary before
# use; no additional checksum step is required here (contrast: install.ps1 + uv_checksums.json).
UV_VERSION="0.11.25"
if ! command -v uv &> /dev/null; then
    echo "[1/4] Downloading uv package manager (pinned ${UV_VERSION})..."
    curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "[1/4] Found existing uv installation."
fi

# 2. Detect GPU Configuration
echo "[2/4] Detecting hardware for optimal routing..."
HARDWARE_FLAG="cpu"
INDEX_URL=""

if command -v nvidia-smi &> /dev/null; then
    echo "      Detected NVIDIA GPU. Configuring for CUDA 12.8."
    HARDWARE_FLAG="nvidia"
    INDEX_URL="--index-url https://download.pytorch.org/whl/cu128"
elif command -v rocm-smi &> /dev/null || lspci | grep -i "vga.*amd" &> /dev/null; then
    echo "      Detected AMD GPU. Configuring for ROCm 7.2."
    HARDWARE_FLAG="amd"
    INDEX_URL="--index-url https://download.pytorch.org/whl/rocm7.2"
else
    echo "      No compatible GPU detected (or macOS). Configuring for CPU/Metal execution."
fi

# 3. Create Isolated Environment
INSTALL_DIR="$HOME/.tensor-grep"
STAGING_INSTALL_DIR="$INSTALL_DIR.installing"
BACKUP_INSTALL_DIR="$INSTALL_DIR.previous"
rm -rf "$STAGING_INSTALL_DIR"
mkdir -p "$STAGING_INSTALL_DIR"

echo "[3/4] Building isolated Python 3.12 environment..."
clear_tensor_grep_uv_cache
cd "$STAGING_INSTALL_DIR"
uv venv --python 3.12 .venv

# 4. Install PyTorch bindings and the tool
echo "[4/4] Installing tensor-grep and ML bindings (this may take a few minutes for CUDA/ROCm)..."
if [ "$INSTALL_CHANNEL" = "main" ]; then
    PKG_SPEC="git+https://github.com/oimiragieo/tensor-grep.git@main"
elif [ -n "$REQUESTED_VERSION" ]; then
    PKG_SPEC="tensor-grep==$REQUESTED_VERSION"
else
    PKG_SPEC="tensor-grep"
fi
echo "      Install source: $INSTALL_CHANNEL"
if [ "$INSTALL_CHANNEL" = "stable" ]; then
    echo "      Package: $PKG_SPEC"
fi

if [ "$HARDWARE_FLAG" != "cpu" ]; then
    uv pip install torch torchvision torchaudio $INDEX_URL --python .venv/bin/python
    # For linux, we also install kvikio and cudf dependencies if NVIDIA
    if [ "$HARDWARE_FLAG" == "nvidia" ]; then
        if [ "$INSTALL_CHANNEL" = "main" ]; then
            PKG_REQUIREMENT="tensor-grep[gpu,nlp,ast] @ $PKG_SPEC"
        else
            PKG_REQUIREMENT="$PKG_SPEC[gpu,nlp,ast]"
        fi
        uv pip install "$PKG_REQUIREMENT" --python .venv/bin/python
    else
        if [ "$INSTALL_CHANNEL" = "main" ]; then
            PKG_REQUIREMENT="tensor-grep[gpu-win,nlp,ast] @ $PKG_SPEC"
        else
            PKG_REQUIREMENT="$PKG_SPEC[gpu-win,nlp,ast]"
        fi
        uv pip install "$PKG_REQUIREMENT" --python .venv/bin/python
    fi
else
    if [ "$INSTALL_CHANNEL" = "main" ]; then
        PKG_REQUIREMENT="tensor-grep[ast,nlp] @ $PKG_SPEC"
    else
        PKG_REQUIREMENT="$PKG_SPEC[ast,nlp]"
    fi
    uv pip install "$PKG_REQUIREMENT" --python .venv/bin/python
fi

# Ensure AST runtime grammars are present explicitly across environments.
uv pip install tree-sitter tree-sitter-python tree-sitter-javascript --python .venv/bin/python

# 5. Prepare the front-door wrapper inside the staged install before replacing
# the existing managed directory. A failed native download or interrupted shim
# write must not leave public shims pointing at a half-built install.
mkdir -p "$STAGING_INSTALL_DIR/bin"
INSTALLED_VERSION="$("$STAGING_INSTALL_DIR/.venv/bin/python" -c 'import importlib.metadata; print(importlib.metadata.version("tensor-grep"))')"
NATIVE_BINARY="$INSTALL_DIR/bin/tg-native"
STAGING_NATIVE_BINARY="$STAGING_INSTALL_DIR/bin/tg-native"
STAGING_NATIVE_METADATA="$STAGING_INSTALL_DIR/bin/tg-native-metadata.json"
TG_NATIVE_FRONTDOOR_FLAVOR="cpu"
TG_NATIVE_FRONTDOOR_ASSET_NAME=""
TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="${TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR:-${TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR:-cpu}}"
TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="$(printf '%s' "$TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR" | tr '[:upper:]' '[:lower:]')"
case "$TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR" in
    nvidia|cuda)
        TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="nvidia"
        ;;
    cpu)
        TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="cpu"
        ;;
    *)
        TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="cpu"
        ;;
esac
if [ "$INSTALL_CHANNEL" != "main" ]; then
    NATIVE_CHECKSUMS_FILE="$STAGING_INSTALL_DIR/bin/CHECKSUMS.txt"
    NATIVE_CHECKSUMS_URL="https://github.com/oimiragieo/tensor-grep/releases/download/v${INSTALLED_VERSION}/CHECKSUMS.txt"
    if ! curl -fLsS "$NATIVE_CHECKSUMS_URL" -o "$NATIVE_CHECKSUMS_FILE"; then
        NATIVE_CHECKSUMS_FILE=""
        echo "      Could not fetch CHECKSUMS.txt; native front door will be skipped (Python fallback)." >&2
    fi
    for NATIVE_FLAVOR in $(native_frontdoor_asset_candidates); do
        NATIVE_ASSET=""
        case "$(uname -s):$(uname -m):$NATIVE_FLAVOR" in
            Linux:x86_64:nvidia|Linux:amd64:nvidia)
                NATIVE_ASSET="tg-linux-amd64-nvidia"
                ;;
            Linux:x86_64:cpu|Linux:amd64:cpu)
                NATIVE_ASSET="tg-linux-amd64-cpu"
                ;;
            Darwin:x86_64:cpu|Darwin:amd64:cpu)
                NATIVE_ASSET="tg-macos-amd64-cpu"
                ;;
            Darwin:*:nvidia)
                continue
                ;;
            *)
                echo "      No release-native tg asset for $(uname -s)/$(uname -m); using Python front door."
                ;;
        esac
        if [ -z "$NATIVE_ASSET" ]; then
            continue
        fi
        NATIVE_URL="https://github.com/oimiragieo/tensor-grep/releases/download/v${INSTALLED_VERSION}/${NATIVE_ASSET}"
        echo "      Downloading native tg front door asset flavor ${NATIVE_FLAVOR}: $NATIVE_ASSET"
        if curl -fL "$NATIVE_URL" -o "$STAGING_NATIVE_BINARY.tmp"; then
            if ! _verify_native_asset "$STAGING_NATIVE_BINARY.tmp" "$NATIVE_CHECKSUMS_FILE" "$NATIVE_ASSET"; then
                rm -f "$STAGING_NATIVE_BINARY.tmp"
                if [ "$NATIVE_FLAVOR" = "nvidia" ]; then
                    echo "      Falling back to CPU native tg front-door asset after NVIDIA checksum verification failed." >&2
                    continue
                fi
                echo "      Native tg front-door checksum verification failed; using Python fallback." >&2
                continue
            fi
            mv "$STAGING_NATIVE_BINARY.tmp" "$STAGING_NATIVE_BINARY"
            chmod +x "$STAGING_NATIVE_BINARY"
            if "$STAGING_NATIVE_BINARY" --version; then
                TG_NATIVE_FRONTDOOR_FLAVOR="$NATIVE_FLAVOR"
                TG_NATIVE_FRONTDOOR_ASSET_NAME="$NATIVE_ASSET"
                echo "      Native tg front door installed: $NATIVE_BINARY (asset flavor: $NATIVE_FLAVOR)"
                break
            else
                rm -f "$STAGING_NATIVE_BINARY"
                if [ "$NATIVE_FLAVOR" = "nvidia" ]; then
                    echo "      Falling back to CPU native tg front-door asset after NVIDIA smoke test failed: $NATIVE_URL" >&2
                    continue
                fi
                echo "      Native tg front-door smoke test failed; using Python fallback." >&2
            fi
        else
            rm -f "$STAGING_NATIVE_BINARY.tmp"
            if [ "$NATIVE_FLAVOR" = "nvidia" ]; then
                echo "      Falling back to CPU native tg front-door asset after NVIDIA asset failed: $NATIVE_URL" >&2
                continue
            fi
            echo "      Native tg front-door download failed; using Python fallback: $NATIVE_URL" >&2
        fi
    done
else
    echo "      Main-channel install: using Python front door until release-native assets exist."
fi
if [ -x "$STAGING_NATIVE_BINARY" ]; then
    cat > "$STAGING_NATIVE_METADATA" << EOF
{
  "artifact": "tensor_grep_native_frontdoor_metadata",
  "asset_flavor": "$TG_NATIVE_FRONTDOOR_FLAVOR",
  "asset_name": "$TG_NATIVE_FRONTDOOR_ASSET_NAME",
  "requested_asset_flavor": "$TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR",
  "version": "$INSTALLED_VERSION"
}
EOF
fi
cat > "$STAGING_INSTALL_DIR/bin/tg" << EOF
#!/usr/bin/env bash
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export TG_SIDECAR_PYTHON="$INSTALL_DIR/.venv/bin/python"
export TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR="$TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR"
export TG_NATIVE_FRONTDOOR_FLAVOR="$TG_NATIVE_FRONTDOOR_FLAVOR"
NATIVE_BINARY="$NATIVE_BINARY"
if [ -x "\$NATIVE_BINARY" ]; then
    export TG_NATIVE_TG_BINARY="\$NATIVE_BINARY"
    exec "\$NATIVE_BINARY" "\$@"
fi
exec "$INSTALL_DIR/.venv/bin/python" -m tensor_grep "\$@"
EOF
chmod +x "$STAGING_INSTALL_DIR/bin/tg"

cd "$HOME"
commit_staged_install
cd "$INSTALL_DIR"

echo "      Installing managed external LSP providers..."
if "$INSTALL_DIR/bin/tg" lsp-setup --json > /dev/null; then
  echo "      Managed external LSP providers installed."
else
  echo "      Managed external LSP provider setup failed; run 'tg lsp-setup' manually." >&2
fi

SHIM_DIRS=("$HOME/.local/bin" "$HOME/bin")
INSTALLED_SHIMS=()
for SHIM_DIR in "${SHIM_DIRS[@]}"; do
    mkdir -p "$SHIM_DIR"
    SHIM_PATH="$SHIM_DIR/tg"
    cat > "$SHIM_PATH" << EOF
#!/usr/bin/env bash
"$INSTALL_DIR/bin/tg" "\$@"
EOF
    chmod +x "$SHIM_PATH"
    INSTALLED_SHIMS+=("$SHIM_PATH")
done

# 6. Add Alias and PATH wiring to User Profile
PROFILE_FILE=""
if [ -n "$BASH_VERSION" ]; then
    PROFILE_FILE="$HOME/.bashrc"
elif [ -n "$ZSH_VERSION" ]; then
    PROFILE_FILE="$HOME/.zshrc"
else
    PROFILE_FILE="$HOME/.profile"
fi

ALIAS_CMD="alias tg='$INSTALL_DIR/bin/tg'"
PATH_EXPORT_LOCAL='export PATH="$HOME/.local/bin:$PATH"'
PATH_EXPORT_BIN='export PATH="$HOME/bin:$PATH"'

touch "$PROFILE_FILE"

if [ -f "$PROFILE_FILE" ] && grep -qE '^[[:space:]]*alias[[:space:]]+tg=' "$PROFILE_FILE"; then
    # Replace any existing tg alias to avoid stale paths/versions.
    TMP_PROFILE="${PROFILE_FILE}.tg.tmp"
    sed -E "s|^[[:space:]]*alias[[:space:]]+tg=.*$|$ALIAS_CMD|" "$PROFILE_FILE" > "$TMP_PROFILE"
    mv "$TMP_PROFILE" "$PROFILE_FILE"
    echo -e "\nSuccessfully installed tensor-grep! Updated existing tg alias in $PROFILE_FILE."
else
    echo -e "\n# Tensor-Grep Alias" >> "$PROFILE_FILE"
    echo "$ALIAS_CMD" >> "$PROFILE_FILE"
    echo -e "\nSuccessfully installed tensor-grep! Added tg alias to $PROFILE_FILE."
fi

if ! grep -qF "$PATH_EXPORT_LOCAL" "$PROFILE_FILE"; then
    echo "$PATH_EXPORT_LOCAL" >> "$PROFILE_FILE"
fi
if ! grep -qF "$PATH_EXPORT_BIN" "$PROFILE_FILE"; then
    echo "$PATH_EXPORT_BIN" >> "$PROFILE_FILE"
fi

# Ensure the current shell session also resolves tg to the fresh install.
alias tg="$INSTALL_DIR/bin/tg"
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
echo "Current session alias now points to: $(command -v tg)"
echo "Installed PATH shims:"
for SHIM_PATH in "${INSTALLED_SHIMS[@]}"; do
    echo "  - $SHIM_PATH"
done
echo "If your shell doesn't apply aliases in non-interactive mode, run: source $PROFILE_FILE"

echo "=========================================================="
echo " Installation complete! Try running: tg search \"ERROR\" ."
"$INSTALL_DIR/bin/tg" --version || true
echo "=========================================================="
