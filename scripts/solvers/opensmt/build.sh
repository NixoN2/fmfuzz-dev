#!/bin/bash
# OpenSMT Build and Test Script
# This script clones, builds, and tests OpenSMT following the release build process

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  libgmp-dev \
  flex \
  bison

echo "📥 Cloning OpenSMT repository..."
git clone https://github.com/usi-verification-and-security/opensmt.git opensmt

echo "🔨 Building OpenSMT (release configuration)..."
cd opensmt

# Set environment variables to match release build
export CMAKE_BUILD_TYPE="Release"
export PARALLEL="OFF"
export ENABLE_LINE_EDITING="FALSE"

# Clean and create build directory
if [ -d build ]; then rm -rf build; fi
mkdir -p build
cd build

# Configure with release settings (following build_maximally_static.sh)
cmake -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE} \
      -DENABLE_LINE_EDITING:BOOL=${ENABLE_LINE_EDITING} \
      -DMAXIMALLY_STATIC_BINARY=YES \
      -DCMAKE_EXE_LINKER_FLAGS=-static \
      -DPARALLEL:BOOL=${PARALLEL} \
      ..

# Build with 4 jobs (matching release script)
cmake --build . -j 4

# Install to system
sudo cmake --install .

echo "🧪 Testing OpenSMT binary..."
opensmt --version

echo "✅ OpenSMT build and test completed successfully!"
