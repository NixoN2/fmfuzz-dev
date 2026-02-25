#!/bin/bash
# Bitwuzla Build and Test Script
# This script clones, builds, and tests Bitwuzla

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  git \
  libgmp-dev \
  meson \
  ninja-build \
  python3 \
  python3-pip

echo "📥 Cloning Bitwuzla repository..."
git clone https://github.com/bitwuzla/bitwuzla.git bitwuzla

echo "🔧 Setting up Python environment..."
python3 -m venv ~/.venv
source ~/.venv/bin/activate
python3 -m pip install meson pytest cython>=3.*

echo "🔨 Building Bitwuzla..."
cd bitwuzla
meson wrap install gtest
./configure.py --testing --unit-testing --python
cd build
ninja
sudo ninja install

echo "🧪 Testing Bitwuzla binary..."
./src/main/bitwuzla --version

echo "✅ Bitwuzla build and test completed successfully!"
