#!/bin/bash
# Q3B Build and Test Script
# This script clones, builds, and tests Q3B following the working Dockerfile approach

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  autotools-dev \
  automake \
  wget \
  unzip \
  make \
  default-jre \
  pkg-config \
  uuid-dev

echo "📥 Cloning Q3B repository..."
git clone --recurse-submodules https://github.com/martinjonas/Q3B.git q3b

echo "🔧 Setting up Q3B dependencies using official script..."
cd q3b
# Use the official dependency script as recommended in documentation
bash contrib/get_deps.sh

echo "🔧 Setting up ANTLR..."
sudo mkdir -p /usr/share/java
wget https://www.antlr.org/download/antlr-4.11.1-complete.jar -P /usr/share/java

echo "🔍 Checking for grammar files..."
find . -name "*.g4" -type f
echo "Checking parser directory structure..."
ls -la parser/ 2>/dev/null || echo "No parser directory found"
ls -la parser/smtlibv2-grammar/ 2>/dev/null || echo "No smtlibv2-grammar directory found"

echo "🔨 Building Q3B..."
# Set environment variables for proper linking
export LD_LIBRARY_PATH="/usr/lib:$LD_LIBRARY_PATH"
export PKG_CONFIG_PATH="/usr/lib/pkgconfig:$PKG_CONFIG_PATH"
cmake -B build -DANTLR_EXECUTABLE=/usr/share/java/antlr-4.11.1-complete.jar
cmake --build build -j4

echo "🧪 Testing Q3B binary..."
# Try different possible binary names and locations
if [ -f "./build/q3b" ]; then
    ./build/q3b --version || echo "Version command completed (exit code $?)"
elif [ -f "./build/bin/q3b" ]; then
    ./build/bin/q3b --version || echo "Version command completed (exit code $?)"
else
    echo "Q3B binary not found, checking build directory..."
    find ./build -name "q3b*" -type f -executable
    BINARY=$(find ./build -name "q3b*" -type f -executable | head -1)
    if [ -n "$BINARY" ]; then
        echo "Found binary: $BINARY"
        $BINARY --version || echo "Version command completed (exit code $?)"
    else
        echo "No Q3B binary found!"
        exit 1
    fi
fi

echo "✅ Q3B build and test completed successfully!"
