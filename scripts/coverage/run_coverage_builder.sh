#!/bin/bash
# Unified coverage analysis script for a test range
# Usage: ./run_coverage_builder.sh --solver <solver> [--test-dir <dir>] START_INDEX END_INDEX

set -e

# Parse flags
SOLVER=""
TEST_DIR_ARG=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --solver)
            SOLVER="$2"
            shift 2
            ;;
        --test-dir)
            TEST_DIR_ARG="--test-dir $2"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

if [ -z "$SOLVER" ]; then
    echo "Error: --solver flag is required"
    echo "Usage: $0 --solver <solver> [--test-dir <dir>] START_INDEX END_INDEX"
    exit 1
fi

START_INDEX=$1
END_INDEX=$2

echo "Running coverage analysis for $SOLVER tests ${START_INDEX}-${END_INDEX}"

# Set test timeout environment variable
export TEST_TIMEOUT=120

# Resolve script paths
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Change to build directory
cd "$SOLVER/build"

# Run coverage analysis - always exit with 0 to prevent GitHub Actions from stopping
# (the Python script already handles all errors gracefully)
python3 "$SCRIPT_DIR/coverage_mapper.py" \
    --solver "$SOLVER" \
    --build-dir . \
    $TEST_DIR_ARG \
    --start-index ${START_INDEX} \
    --end-index ${END_INDEX} || true

echo "Coverage analysis completed"
exit 0
