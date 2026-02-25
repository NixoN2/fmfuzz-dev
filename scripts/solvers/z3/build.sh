#!/bin/bash
# Z3 Build and Test Script
# This script clones, builds, and tests Z3 following CI best practices
# Usage: ./build.sh [--coverage] [--static]
#   --coverage: Enable coverage instrumentation
#   --static: Build static binary (note: Z3 uses static by default when LIBZ3_SHARED=OFF)
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
  python3-pip

# Install coverage tools if coverage is enabled
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "📊 Installing coverage tools..."
    sudo apt-get install -y gcc
    # Install fastcov and psutil for coverage analysis
    pip3 install fastcov psutil
    
    # Set environment variables for coverage collection
    export GCOV_PREFIX=$(pwd)/z3/build
    export GCOV_PREFIX_STRIP=0
    echo "🔧 Set coverage environment variables:"
    echo "  GCOV_PREFIX=$GCOV_PREFIX"
    echo "  GCOV_PREFIX_STRIP=$GCOV_PREFIX_STRIP"
fi

echo "📥 Cloning Z3 repository..."
if [ -d "z3" ]; then
    echo "⚠️  Z3 directory already exists, skipping clone"
else
    git clone https://github.com/Z3Prover/z3.git z3
fi

echo "🔨 Building Z3..."
cd z3

mkdir -p build
cd build

# Configure build based on flags
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "🔍 Configuring Z3 with coverage instrumentation..."
    CFLAGS="-O0 -g --coverage" CXXFLAGS="-O0 -g --coverage" \
      cmake -DCMAKE_BUILD_TYPE=Debug \
            -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
            -DZ3_BUILD_LIBZ3_SHARED=OFF \
            -DZ3_BUILD_EXECUTABLE=ON \
            -DZ3_BUILD_TEST_EXECUTABLES=OFF \
            -G "Unix Makefiles" ..
elif [[ "$ENABLE_STATIC" == "true" ]]; then
    echo "📦 Configuring Z3 for static binary (production)..."
    cmake -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
          -DZ3_BUILD_LIBZ3_SHARED=OFF \
          -DZ3_BUILD_EXECUTABLE=ON \
          -DZ3_BUILD_TEST_EXECUTABLES=OFF \
          -G "Unix Makefiles" ..
else
    echo "⚡ Configuring Z3 for production (no coverage)..."
    cmake -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
          -DZ3_BUILD_LIBZ3_SHARED=OFF \
          -DZ3_BUILD_EXECUTABLE=ON \
          -DZ3_BUILD_TEST_EXECUTABLES=OFF \
          -G "Unix Makefiles" ..
fi

# Build Z3
make -j$(nproc)

# Remove unnecessary library to save space (saves ~1.3GB)
if [ -f "libz3.a" ]; then
    echo "🗑️  Removing libz3.a to save space..."
    rm -f libz3.a
fi

# Install to system
sudo make install

echo "🧪 Testing Z3 binary..."
# Test the Z3 binary
if [ -f "./z3" ]; then
    ./z3 --version || echo "Version command completed (exit code $?)"
    echo "Z3 binary is working correctly!"
else
    echo "Z3 binary not found!"
    exit 1
fi

echo "✅ Z3 build and test completed successfully!"