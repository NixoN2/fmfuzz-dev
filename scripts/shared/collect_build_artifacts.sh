#!/bin/bash
# Collect build artifacts for libclang parsing
# This script collects:
# - All header files (.h, .hpp, .hxx) from build directory with preserved paths
# - The solver binary
# - compile_commands.json
#
# Usage: ./collect_build_artifacts.sh --solver <solver> <build_dir> <output_dir>
# Example: ./collect_build_artifacts.sh --solver z3 z3/build artifacts

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
    echo "Usage: $0 --solver <solver> [build_dir] [output_dir]"
    exit 1
fi

# Load solver config
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOLVER_CONFIG="$SCRIPT_DIR/../solvers/$SOLVER/solver.json"

if [ ! -f "$SOLVER_CONFIG" ]; then
    echo "Error: Solver config not found: $SOLVER_CONFIG"
    exit 1
fi

# Read config values using python (portable JSON parsing)
BINARY_SUBPATH=$(python3 -c "import json; c=json.load(open('$SOLVER_CONFIG')); print(c['artifacts']['binary_subpath'])")
HEADER_DIRS=$(python3 -c "import json; c=json.load(open('$SOLVER_CONFIG')); print(' '.join(c['artifacts']['header_dirs']))")
DEPS_DIRS=$(python3 -c "import json; c=json.load(open('$SOLVER_CONFIG')); print(' '.join(c['artifacts'].get('deps_dirs', [])))")

BUILD_DIR="${1:-$SOLVER/build}"
OUTPUT_DIR="${2:-artifacts}"

if [ ! -d "$BUILD_DIR" ]; then
    echo "Error: Build directory not found: $BUILD_DIR"
    exit 1
fi

echo "Collecting build artifacts from $BUILD_DIR"
echo "   Solver: $SOLVER"
echo "   Output directory: $OUTPUT_DIR"

# Create output directory structure
mkdir -p "$OUTPUT_DIR/headers"
mkdir -p "$OUTPUT_DIR/bin"

# Collect all header files with preserved directory structure
echo "Collecting header files..."

# Collect from standard header directories
for dir in $HEADER_DIRS; do
    if [ -d "$BUILD_DIR/$dir" ]; then
        find "$BUILD_DIR/$dir" -type f \( -name "*.h" -o -name "*.hpp" -o -name "*.hxx" \) | while read -r header; do
            rel_path="${header#$BUILD_DIR/}"
            target_path="$OUTPUT_DIR/headers/$rel_path"
            mkdir -p "$(dirname "$target_path")"
            cp "$header" "$target_path"
        done
        COUNT=$(find "$BUILD_DIR/$dir" -type f \( -name "*.h" -o -name "*.hpp" -o -name "*.hxx" \) 2>/dev/null | wc -l)
        echo "   Collected $COUNT headers from $dir/"
    fi
done

# Collect from dependency directories (e.g., cvc5 deps)
for dir in $DEPS_DIRS; do
    if [ -d "$BUILD_DIR/$dir" ]; then
        find "$BUILD_DIR/$dir" -type f \( -name "*.h" -o -name "*.hpp" -o -name "*.hxx" \) | while read -r header; do
            rel_path="${header#$BUILD_DIR/}"
            target_path="$OUTPUT_DIR/headers/$rel_path"
            mkdir -p "$(dirname "$target_path")"
            cp "$header" "$target_path"
        done
        COUNT=$(find "$BUILD_DIR/$dir" -type f \( -name "*.h" -o -name "*.hpp" -o -name "*.hxx" \) 2>/dev/null | wc -l)
        echo "   Collected $COUNT headers from $dir/"
    fi
done

# Count total headers
TOTAL_HEADERS=$(find "$OUTPUT_DIR/headers" -type f 2>/dev/null | wc -l || echo "0")
echo "   Total headers collected: $TOTAL_HEADERS"

# Copy binary
BINARY_SRC="$BUILD_DIR/$BINARY_SUBPATH"
BINARY_DST="$OUTPUT_DIR/bin/$(basename "$BINARY_SUBPATH")"
if [ -f "$BINARY_SRC" ]; then
    mkdir -p "$(dirname "$BINARY_DST")"
    cp "$BINARY_SRC" "$BINARY_DST"
    chmod +x "$BINARY_DST"
    BINARY_SIZE=$(du -h "$BINARY_DST" | cut -f1)
    echo "   Binary copied ($BINARY_SIZE)"
else
    echo "   Warning: Binary not found at $BINARY_SRC"
fi

# Copy compile_commands.json
if [ -f "$BUILD_DIR/compile_commands.json" ]; then
    cp "$BUILD_DIR/compile_commands.json" "$OUTPUT_DIR/compile_commands.json"
    echo "   compile_commands.json copied"
else
    echo "   Warning: compile_commands.json not found at $BUILD_DIR/compile_commands.json"
fi

# Create summary
echo ""
echo "Artifact collection complete!"
echo "   Headers: $OUTPUT_DIR/headers/"
echo "   Binary: $BINARY_DST"
echo "   Compile commands: $OUTPUT_DIR/compile_commands.json"
echo ""
echo "Summary:"
echo "   Total header files: $TOTAL_HEADERS"
if [ -f "$BINARY_DST" ]; then
    echo "   Binary: yes"
else
    echo "   Binary: no"
fi
if [ -f "$OUTPUT_DIR/compile_commands.json" ]; then
    echo "   compile_commands.json: yes"
else
    echo "   compile_commands.json: no"
fi
