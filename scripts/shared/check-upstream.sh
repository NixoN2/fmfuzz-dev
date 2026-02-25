#!/bin/bash
# Generic script to check if upstream repository has new commits
# Usage: ./scripts/shared/check-upstream.sh <solver-name> <repo-url>

set -e

SOLVER_NAME="$1"
REPO_URL="$2"

if [ -z "$SOLVER_NAME" ] || [ -z "$REPO_URL" ]; then
    echo "Usage: $0 <solver-name> <repo-url>"
    echo "Example: $0 cvc5 https://github.com/cvc5/cvc5.git"
    exit 1
fi

echo "🔍 Checking upstream for $SOLVER_NAME..."

# Get the latest commit SHA from upstream
LATEST_SHA=$(git ls-remote "$REPO_URL" HEAD | cut -f1)
echo "📡 Latest $SOLVER_NAME commit: $LATEST_SHA"

# Ensure cache directory exists
mkdir -p .cache

# Check if we have a cached SHA for this solver
CACHE_FILE=".cache/${SOLVER_NAME}_last_sha"
if [ -f "$CACHE_FILE" ]; then
    LAST_SHA=$(cat "$CACHE_FILE")
    echo "💾 Last built $SOLVER_NAME SHA: $LAST_SHA"
    
    if [ "$LAST_SHA" = "$LATEST_SHA" ]; then
        echo "✅ $SOLVER_NAME is up to date - no build needed"
        echo "build_needed=false" > .build_status
        echo "sha=$LATEST_SHA" >> .build_status
        exit 0
    else
        echo "🔄 $SOLVER_NAME has new commits - build needed"
        echo "build_needed=true" > .build_status
        echo "sha=$LATEST_SHA" >> .build_status
        # Don't save SHA here - let build jobs save it after successful build
    fi
else
    echo "🆕 First time checking $SOLVER_NAME - build needed"
    echo "build_needed=true" > .build_status
    echo "sha=$LATEST_SHA" >> .build_status
    # Don't save SHA here - let build jobs save it after successful build
fi

# Note: SHA is saved by build jobs after successful builds, not here
# This ensures we only mark commits as "built" after they're actually built

# Output the build status
cat .build_status
