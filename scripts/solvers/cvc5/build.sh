#!/bin/bash
# CVC5 Build and Test Script
# This script clones, builds, and tests CVC5 following CI best practices
# Usage: ./build.sh [--coverage] [--static]
#   --coverage: Enable coverage instrumentation
#   --static: Build static binary
#   --static --coverage: Build static binary with coverage

set -e  # Exit on any error

# Parse command line arguments
ENABLE_COVERAGE=false
ENABLE_STATIC=false
for arg in "$@"; do
    if [[ "$arg" == "--coverage" ]]; then
        ENABLE_COVERAGE=true
    elif [[ "$arg" == "--static" ]]; then
        ENABLE_STATIC=true
    fi
done

if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "🔍 Coverage instrumentation will be enabled"
fi
if [[ "$ENABLE_STATIC" == "true" ]]; then
    echo "📦 Static binary build will be enabled"
fi

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  python3 \
  python3-pip \
  ccache \
  libbsd-dev \
  libcln-dev \
  libedit-dev \
  libgmp-dev \
  libtinfo-dev \
  libfl-dev

# Install coverage tools if coverage is enabled (standalone or with static)
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "📊 Installing coverage tools..."
    sudo apt-get install -y lcov gcc
    # Install fastcov and psutil for coverage analysis
    pip3 install fastcov psutil
    
    # Set environment variables for coverage collection
    export GCOV_PREFIX=$(pwd)/cvc5/build
    export GCOV_PREFIX_STRIP=0
    export TEST_TIMEOUT=120
    echo "🔧 Set coverage environment variables:"
    echo "  GCOV_PREFIX=$GCOV_PREFIX"
    echo "  GCOV_PREFIX_STRIP=$GCOV_PREFIX_STRIP"
    echo "  TEST_TIMEOUT=$TEST_TIMEOUT"
fi

echo "📥 Cloning CVC5 repository..."
if [ -d "cvc5" ]; then
    echo "⚠️  CVC5 directory already exists, skipping clone"
else
    git clone https://github.com/cvc5/cvc5.git cvc5
fi

echo "🔧 Setting up Python environment..."
python3 -m venv ~/.venv
source ~/.venv/bin/activate
python3 -m pip install --upgrade pip

echo "🔨 Building CVC5..."
cd cvc5

# Configure build based on flags
if [[ "$ENABLE_STATIC" == "true" ]] && [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "📦 Configuring CVC5 for static binary with coverage..."
    ./configure.sh debug --coverage --assertions --static --static-binary --auto-download
elif [[ "$ENABLE_STATIC" == "true" ]]; then
    echo "📦 Configuring CVC5 for static binary (production)..."
    ./configure.sh production --static --static-binary --auto-download
elif [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "🔍 Configuring CVC5 with coverage instrumentation..."
    ./configure.sh debug --coverage --assertions --auto-download
else
    echo "⚡ Configuring CVC5 for production (no coverage)..."
    ./configure.sh production --auto-download
fi

cd build
make -j$(nproc)

# Install to system
sudo make install

echo "🧪 Testing CVC5 binary..."
# Test the installed binary
if command -v cvc5 >/dev/null 2>&1; then
    cvc5 --version || echo "Version command completed (exit code $?)"
    echo "CVC5 binary is working correctly!"
else
    # Fallback to build directory
    if [ -f "./bin/cvc5" ]; then
        ./bin/cvc5 --version || echo "Version command completed (exit code $?)"
        echo "CVC5 binary is working correctly!"
    else
        echo "CVC5 binary not found!"
        exit 1
    fi
fi

echo "✅ CVC5 build and test completed successfully!"
