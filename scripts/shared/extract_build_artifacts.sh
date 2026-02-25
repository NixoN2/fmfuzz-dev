#!/bin/bash
# Extract build artifacts from artifacts.tar.gz
# This script extracts:
# - All header files to build/ with preserved paths
# - The solver binary to the correct build path
# - compile_commands.json to build/
#
# Usage: ./extract_build_artifacts.sh --solver <solver> <artifact_file> <build_dir> [extract_headers]
# Example: ./extract_build_artifacts.sh --solver z3 artifacts/artifacts.tar.gz z3/build true
#
# If extract_headers is "true" (default), extracts headers. If "false", only extracts binary.

set -e

# Parse --solver flag
SOLVER=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --solver)
            SOLVER="$2"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

if [ -z "$SOLVER" ]; then
    echo "Error: --solver flag is required"
    echo "Usage: $0 --solver <solver> <artifact_file> [build_dir] [extract_headers]"
    exit 1
fi

# Load solver config
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOLVER_CONFIG="$SCRIPT_DIR/../solvers/$SOLVER/solver.json"

if [ ! -f "$SOLVER_CONFIG" ]; then
    echo "Error: Solver config not found: $SOLVER_CONFIG"
    exit 1
fi

# Read config values
BINARY_SUBPATH=$(python3 -c "import json; c=json.load(open('$SOLVER_CONFIG')); print(c['artifacts']['binary_subpath'])")
HEADER_DIRS=$(python3 -c "import json; c=json.load(open('$SOLVER_CONFIG')); print(' '.join(c['artifacts']['header_dirs']))")
DEPS_DIRS=$(python3 -c "import json; c=json.load(open('$SOLVER_CONFIG')); print(' '.join(c['artifacts'].get('deps_dirs', [])))")

ARTIFACT_FILE="${1}"
BUILD_DIR="${2:-$SOLVER/build}"
EXTRACT_HEADERS="${3:-true}"

if [ -z "$ARTIFACT_FILE" ]; then
    echo "Error: Artifact file not specified"
    exit 1
fi

if [ ! -f "$ARTIFACT_FILE" ]; then
    echo "Error: Artifact file not found: $ARTIFACT_FILE"
    exit 1
fi

echo "Extracting build artifacts from $ARTIFACT_FILE"
echo "   Solver: $SOLVER"
echo "   Build directory: $BUILD_DIR"
echo "   Extract headers: $EXTRACT_HEADERS"

mkdir -p "$BUILD_DIR"

# Extract to temp location
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

echo "Extracting archive..."
tar -xzf "$ARTIFACT_FILE" -C "$TMP_DIR"

# Extract binary
BINARY_NAME=$(basename "$BINARY_SUBPATH")
if [ -f "$TMP_DIR/bin/$BINARY_NAME" ]; then
    BINARY_DIR="$(dirname "$BUILD_DIR/$BINARY_SUBPATH")"
    mkdir -p "$BINARY_DIR"
    cp "$TMP_DIR/bin/$BINARY_NAME" "$BUILD_DIR/$BINARY_SUBPATH"
    chmod +x "$BUILD_DIR/$BINARY_SUBPATH"
    echo "Binary extracted to $BUILD_DIR/$BINARY_SUBPATH"
else
    echo "Warning: Binary not found in artifacts"
fi

# Extract compile_commands.json
if [ -f "$TMP_DIR/compile_commands.json" ]; then
    cp "$TMP_DIR/compile_commands.json" "$BUILD_DIR/compile_commands.json"
    echo "compile_commands.json extracted"
else
    echo "Warning: compile_commands.json not found in artifacts"
fi

# Extract headers if requested
if [ "$EXTRACT_HEADERS" = "true" ]; then
    if [ -d "$TMP_DIR/headers" ]; then
        # Move standard header directories
        for dir in $HEADER_DIRS; do
            if [ -d "$TMP_DIR/headers/$dir" ]; then
                mv "$TMP_DIR/headers/$dir" "$BUILD_DIR/$dir"
                echo "Headers extracted: $dir/"
            fi
        done
        # Move dependency directories
        for dir in $DEPS_DIRS; do
            # deps_dirs may be like "deps/include" - extract top-level dir
            TOP_DIR=$(echo "$dir" | cut -d/ -f1)
            if [ -d "$TMP_DIR/headers/$TOP_DIR" ] && [ ! -d "$BUILD_DIR/$TOP_DIR" ]; then
                mv "$TMP_DIR/headers/$TOP_DIR" "$BUILD_DIR/$TOP_DIR"
                echo "Headers extracted: $TOP_DIR/"
            fi
        done

        # Count headers
        HEADER_COUNT=$(find "$BUILD_DIR" -type f \( -name "*.h" -o -name "*.hpp" -o -name "*.hxx" \) 2>/dev/null | wc -l || echo "0")
        echo "  Total headers: $HEADER_COUNT"
    else
        echo "Warning: headers/ directory not found in artifacts"
    fi
fi

# Verify binary
if [ -f "$BUILD_DIR/$BINARY_SUBPATH" ]; then
    "$BUILD_DIR/$BINARY_SUBPATH" --version > /dev/null 2>&1 && echo "Binary verified" || echo "Binary verification failed"
fi

echo "Extraction complete!"
