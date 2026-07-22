#!/usr/bin/env bash
# Builds a Pillow Lambda layer zip for Python 3.12 on x86_64 Linux.
#
# Pillow ships compiled C extensions, so a wheel built on Windows or macOS
# will fail inside Lambda with "No module named 'PIL._imaging'".
# --platform + --only-binary force pip to fetch the Linux wheel regardless
# of the machine you run this on.
#
# Usage:  bash build-layer.sh
# Output: pillow-layer.zip  (upload this as a Lambda layer)

set -euo pipefail

PYTHON_VERSION="3.12"
BUILD_DIR="build"
OUTPUT="pillow-layer.zip"

rm -rf "$BUILD_DIR" "$OUTPUT"
mkdir -p "$BUILD_DIR/python"

echo "Downloading Linux x86_64 Pillow wheel for Python ${PYTHON_VERSION}..."
pip install \
  --platform manylinux2014_x86_64 \
  --target="$BUILD_DIR/python" \
  --implementation cp \
  --python-version "$PYTHON_VERSION" \
  --only-binary=:all: \
  --upgrade \
  Pillow

# Trim files Lambda never needs — keeps the layer under the 50 MB limit.
find "$BUILD_DIR/python" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$BUILD_DIR/python" -type d -name "tests" -prune -exec rm -rf {} +
find "$BUILD_DIR/python" -type d -name "*.dist-info" -prune -exec rm -rf {} +

echo "Zipping..."
cd "$BUILD_DIR"
zip -r "../$OUTPUT" python -q
cd ..

echo
echo "Built $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
echo "Layer structure check:"
unzip -l "$OUTPUT" | head -8
echo
echo "The paths above MUST start with 'python/PIL/'. If they don't, Lambda won't find the module."
echo
echo "Publish it with:"
echo "  aws lambda publish-layer-version \\"
echo "    --layer-name pillow-layer \\"
echo "    --zip-file fileb://$OUTPUT \\"
echo "    --compatible-runtimes python${PYTHON_VERSION} \\"
echo "    --compatible-architectures x86_64"
