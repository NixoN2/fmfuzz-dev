#!/bin/bash
# STP Build and Test Script
# This script clones, builds, and tests STP

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  bison \
  build-essential \
  cmake \
  flex \
  git \
  libboost-program-options-dev \
  ninja-build \
  patchelf \
  python3 \
  python3-pip \
  python3-setuptools \
  zlib1g-dev
sudo pip3 install -U lit

echo "📥 Cloning STP repository..."
git clone --recurse-submodules https://github.com/stp/stp.git stp

echo "🔧 Setting up STP dependencies..."
cd stp
./scripts/deps/setup-minisat.sh
./scripts/deps/setup-cms.sh
./scripts/deps/setup-gtest.sh
./scripts/deps/setup-outputcheck.sh

echo "🔨 Building STP..."
mkdir build
cd build
cmake -DNOCRYPTOMINISAT:BOOL=OFF -DENABLE_TESTING:BOOL=ON -DPYTHON_EXECUTABLE:PATH="$(which python3)" -G Ninja ..
cmake --build . --parallel $(nproc)

echo "📦 Installing STP..."
sudo cmake --install .

echo "📦 Installing STP dependencies to system..."
# Copy all dependency libraries to /usr/local/lib
sudo cp -f ../deps/install/lib/*.so* /usr/local/lib/ 2>/dev/null || true
sudo cp -f ../deps/cadical/build/libcadical.so /usr/local/lib/ 2>/dev/null || true
sudo cp -f ../deps/cadiback/libcadiback.so /usr/local/lib/ 2>/dev/null || true
sudo cp -f lib/*.so* /usr/local/lib/ 2>/dev/null || true

echo "🔧 Fixing RPATH in STP binaries..."
# Fix the RPATH in the STP binaries to use /usr/local/lib instead of build directories
sudo patchelf --set-rpath '/usr/local/lib:/lib/x86_64-linux-gnu' /usr/local/bin/stp 2>/dev/null || true
sudo patchelf --set-rpath '/usr/local/lib:/lib/x86_64-linux-gnu' /usr/local/bin/stp_simple 2>/dev/null || true

echo "🔧 Updating library cache..."
sudo ldconfig

echo "🧪 Testing STP binary..."
# Set LD_LIBRARY_PATH to ensure shared libraries are found
export LD_LIBRARY_PATH="/usr/local/lib:$LD_LIBRARY_PATH"
stp --version

echo "✅ STP build and test completed successfully!"
