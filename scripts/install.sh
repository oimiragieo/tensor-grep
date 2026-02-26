#!/usr/bin/env bash

set -e

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
if [ "$HARDWARE_FLAG" != "cpu" ]; then
    uv pip install torch torchvision torchaudio $INDEX_URL --python .venv/bin/python
    # For linux, we also install kvikio and cudf dependencies if NVIDIA
    if [ "$HARDWARE_FLAG" == "nvidia" ]; then
        uv pip install "tensor-grep[gpu,nlp,ast]" --python .venv/bin/python
    else
        uv pip install "tensor-grep[gpu-win,nlp,ast]" --python .venv/bin/python
    fi
else
    uv pip install "tensor-grep[ast,nlp]" --python .venv/bin/python
fi

# 5. Add Alias to User Profile
PROFILE_FILE=""
if [ -n "$BASH_VERSION" ]; then
    PROFILE_FILE="$HOME/.bashrc"
elif [ -n "$ZSH_VERSION" ]; then
    PROFILE_FILE="$HOME/.zshrc"
else
    PROFILE_FILE="$HOME/.profile"
fi

ALIAS_CMD="alias tg='$INSTALL_DIR/.venv/bin/tg'"

if ! grep -q "alias tg=" "$PROFILE_FILE"; then
    echo -e "\n# Tensor-Grep Alias" >> "$PROFILE_FILE"
    echo "$ALIAS_CMD" >> "$PROFILE_FILE"
    echo -e "\nSuccessfully installed tensor-grep!"
    echo "Alias 'tg' added to $PROFILE_FILE."
    echo "Please restart your terminal or run: source $PROFILE_FILE"
else
    echo -e "\nSuccessfully installed tensor-grep! Alias 'tg' already exists in $PROFILE_FILE."
fi

echo "=========================================================="
echo " Installation complete! Try running: tg search \"ERROR\" ."
echo "=========================================================="
