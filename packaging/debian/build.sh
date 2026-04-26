#!/usr/bin/env bash
# Build a .deb from the linux-x86_64 release tarball produced by
# release-binaries.yml. Run from the repo root, after the tarball is
# present at dist/daimon-linux-x86_64.tar.gz.
#
# Usage: VERSION=1.2.3 packaging/debian/build.sh
set -euo pipefail

VERSION="${VERSION:?set VERSION (e.g. VERSION=0.1.0)}"
ARCH="amd64"
PKG_DIR="dist/deb-build/daimon_${VERSION}_${ARCH}"

mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/usr/lib/daimon"

# Extract the standalone tree into /usr/lib/daimon/, then symlink the
# entry-point binaries into /usr/bin/. Keeps the embedded WezTerm
# alongside `daimon` (the runtime resolver looks in
# Path(sys.executable).parent — the symlinks resolve there).
tar xzf dist/daimon-linux-x86_64.tar.gz -C /tmp
cp -r /tmp/daimon-linux-x86_64/* "${PKG_DIR}/usr/lib/daimon/"
ln -sf /usr/lib/daimon/daimon "${PKG_DIR}/usr/bin/daimon"
ln -sf /usr/lib/daimon/dmn-mcp "${PKG_DIR}/usr/bin/dmn-mcp"

# Generate control file with the right Version pin.
sed "s/^Version: .*/Version: ${VERSION}/" \
    packaging/debian/daimon.control \
    > "${PKG_DIR}/DEBIAN/control"

dpkg-deb --build --root-owner-group "${PKG_DIR}"

echo "built: ${PKG_DIR}.deb"
