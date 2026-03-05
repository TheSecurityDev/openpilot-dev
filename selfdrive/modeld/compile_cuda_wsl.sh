#!/bin/bash
# Compile vision and policy model pkl files for CUDA on WSL2.
# Warp pkl files are kept CPU-compiled (from_blob requires host pointers).
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENPILOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODELS_DIR="$SCRIPT_DIR/models"
TINYGRAD_REPO="$OPENPILOT_DIR/tinygrad_repo"

export DEV=CUDA
# CUDA_PATH must be a directory: DLL.findlib searches it for libcuda.so (WSL stub),
# and compiler_cuda.py uses CUDA_PATH/include for NVRTC kernel compilation headers.
# /tmp/wsl_cuda_home has libcuda.so -> WSL stub + include -> /usr/local/cuda/include
mkdir -p /tmp/wsl_cuda_home
ln -sfn /usr/lib/wsl/lib/libcuda.so.1 /tmp/wsl_cuda_home/libcuda.so
ln -sfn /usr/lib/wsl/lib/libcuda.so.1 /tmp/wsl_cuda_home/libcuda.so.1
ln -sfn /usr/local/cuda/include /tmp/wsl_cuda_home/include
export CUDA_PATH=/tmp/wsl_cuda_home
export PYTHONPATH="$PYTHONPATH:$TINYGRAD_REPO"
export JIT_BATCH_SIZE=0
export IMAGE=0
export THREADS=0

source "$OPENPILOT_DIR/.venv/bin/activate"

compile_model() {
  local name="$1"
  local onnx="$MODELS_DIR/${name}.onnx"
  local pkl="$MODELS_DIR/${name}_tinygrad.pkl"
  local tmp="/tmp/${name}_tinygrad_cuda.pkl"

  echo "=== Compiling $name for CUDA ==="
  python3 "$TINYGRAD_REPO/examples/openpilot/compile3.py" "$onnx" "$tmp"

  echo "=== Chunking $name ==="
  python3 - <<EOF
import sys
sys.path.insert(0, '$OPENPILOT_DIR')
from openpilot.common.file_chunker import chunk_file, get_chunk_paths
import os, shutil

src = '$tmp'
pkl = '$pkl'
onnx_size = os.path.getsize('$onnx')
chunk_targets = get_chunk_paths(pkl, int(1.2 * onnx_size + 10 * 1024 * 1024))

# Remove old chunks
for t in chunk_targets:
    if os.path.exists(t):
        os.remove(t)

shutil.copy(src, pkl)
chunk_file(pkl, chunk_targets)
os.remove(src)
print(f"Chunked into {len(chunk_targets)-1} data files")
EOF
  echo "=== Done: $name ==="
}

# Vision and policy models: compiled for CUDA
# img inputs (in compile3.py) are placed on Device.DEFAULT=CUDA during JIT tracing.
# At runtime, modeld.py (WSL_CUDA path) copies warp outputs to CUDA before calling vision model.
# Policy model inputs are NPY; the compiled JIT transfers them to CUDA internally.
compile_model driving_vision
compile_model driving_policy
compile_model dmonitoring_model

echo ""
echo "=== All models compiled for CUDA ==="
echo "Run modeld with: WSL_CUDA=1 python selfdrive/modeld/modeld.py"
