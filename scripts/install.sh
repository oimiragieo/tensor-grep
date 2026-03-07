#!/usr/bin/env bash

set -e

ORIGINAL_DIR="$(pwd)"
INSTALL_CHANNEL="${TENSOR_GREP_CHANNEL:-stable}"
REQUESTED_VERSION="${TENSOR_GREP_VERSION:-}"

restore_original_dir() {
    cd "$ORIGINAL_DIR" || return
    echo "Returned to original directory: $ORIGINAL_DIR"
}

trap restore_original_dir EXIT

echo "=========================================================="
echo "           TENSOR-GREP LINUX/MACOS INSTALLER              "
echo "=========================================================="

# 1. Install or locate uv
if ! command -v uv &> /dev/null; then
    echo "[1/4] Downloading uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "[1/4] Found existing uv installation."
fi

# 2. Detect GPU Configuration
echo "[2/4] Detecting hardware for optimal routing..."
HARDWARE_FLAG="cpu"
INDEX_URL=""

if command -v nvidia-smi &> /dev/null; then
    echo "      Detected NVIDIA GPU. Configuring for CUDA 12.4."
    HARDWARE_FLAG="nvidia"
    INDEX_URL="--index-url https://download.pytorch.org/whl/cu124"
elif command -v rocm-smi &> /dev/null || lspci | grep -i "vga.*amd" &> /dev/null; then
    echo "      Detected AMD GPU. Configuring for ROCm."
    HARDWARE_FLAG="amd"
    INDEX_URL="--index-url https://download.pytorch.org/whl/rocm6.0"
else
    echo "      No compatible GPU detected (or macOS). Configuring for CPU/Metal execution."
fi

# 3. Create Isolated Environment
INSTALL_DIR="$HOME/.tensor-grep"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

echo "[3/4] Building isolated Python 3.12 environment..."
cd "$INSTALL_DIR"
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

# 5. Install PATH shims for profile-independent command resolution.
SHIM_DIRS=("$HOME/.local/bin" "$HOME/bin")
INSTALLED_SHIMS=()
for SHIM_DIR in "${SHIM_DIRS[@]}"; do
    mkdir -p "$SHIM_DIR"
    SHIM_PATH="$SHIM_DIR/tg"
    cat > "$SHIM_PATH" << EOF
#!/usr/bin/env bash
"$INSTALL_DIR/.venv/bin/tg" "\$@"
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

ALIAS_CMD="alias tg='$INSTALL_DIR/.venv/bin/tg'"
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
alias tg="$INSTALL_DIR/.venv/bin/tg"
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"
echo "Current session alias now points to: $(command -v tg)"
echo "Installed PATH shims:"
for SHIM_PATH in "${INSTALLED_SHIMS[@]}"; do
    echo "  - $SHIM_PATH"
done
echo "If your shell doesn't apply aliases in non-interactive mode, run: source $PROFILE_FILE"

echo "=========================================================="
echo " Installation complete! Try running: tg search \"ERROR\" ."
./.venv/bin/tg --version || true
echo "=========================================================="
