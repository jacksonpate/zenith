# Building Zenith locally (Linux Mint 22.x / Ubuntu 24.04, NVIDIA)

Reference recipe for the dev box (`pate-pc-lm`: Mint 22.3, GTX 1660 Ti, system CUDA 12.0
from `nvidia-cuda-toolkit`). `scripts/linux_build.sh` detects Mint as of the Zenith fork,
but its cmake step assumes a CUDA toolkit that accepts gcc-14 as host compiler (CUDA ≥ 12.8
/ the 13.1 runfile). With the Ubuntu-archive CUDA 12.0, use this instead:

```bash
sudo apt install build-essential ninja-build gcc-14 g++-14 gcc-12 g++-12 \
  nvidia-cuda-toolkit glslang-tools libvulkan-dev qt6-base-dev qt6-svg-dev \
  python3-jinja2 python3-setuptools appstream appstream-util desktop-file-utils \
  libudev-dev libsystemd-dev systemd-dev libayatana-appindicator3-dev \
  libboost-filesystem-dev libboost-locale-dev libboost-log-dev libboost-program-options-dev \
  libcap-dev libcurl4-openssl-dev libdrm-dev libevdev-dev libgbm-dev libminiupnpc-dev \
  libnotify-dev libnuma-dev libopus-dev libpulse-dev libssl-dev libva-dev libvdpau-dev \
  libwayland-dev libx11-dev libxcb-shm0-dev libxcb-xfixes0-dev libxcb1-dev libxfixes-dev \
  libxrandr-dev libxtst-dev libpipewire-0.3-dev libdbus-1-dev nodejs npm

export CC=gcc-14 CXX=g++-14
cmake -B build -G Ninja -S . \
  -DBUILD_WERROR=ON -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr -DSUNSHINE_ASSETS_DIR=share/sunshine \
  -DSUNSHINE_EXECUTABLE_PATH=/usr/bin/sunshine \
  -DSUNSHINE_ENABLE_DRM=ON -DSUNSHINE_ENABLE_KWIN=ON -DSUNSHINE_ENABLE_PORTAL=ON \
  -DSUNSHINE_ENABLE_WAYLAND=ON -DSUNSHINE_ENABLE_X11=ON -DBUILD_DOCS=OFF \
  -DSUNSHINE_ENABLE_CUDA=ON \
  -DCMAKE_CUDA_COMPILER:PATH=/usr/bin/nvcc \
  -DCMAKE_CUDA_HOST_COMPILER=gcc-12 \
  -DCMAKE_EXE_LINKER_FLAGS="-L/usr/lib/gcc/x86_64-linux-gnu/14"
ninja -C build
```

Why the two odd flags:

- **`CMAKE_CUDA_HOST_COMPILER=gcc-12`** — nvcc 12.0 rejects gcc > 12 as host compiler.
- **`CMAKE_EXE_LINKER_FLAGS=-L/usr/lib/gcc/x86_64-linux-gnu/14`** — CMake propagates the
  CUDA host compiler's implicit link dir (`.../gcc/12`) onto the link line ahead of gcc-14's.
  Combined with `-static-libstdc++` that statically links gcc-12's libstdc++, which lacks the
  `GLIBCXX_3.4.31` symbols emitted by the gcc-14-compiled objects (`_M_replace_cold` etc.),
  failing the final link. Forcing gcc-14's libdir first fixes resolution.

Neither flag is needed when building with the CUDA 13.1 runfile
(`./scripts/linux_build.sh --cuda-runfile`), which accepts gcc-14 directly.

KMS capture needs capabilities on the binary; the web UI then works from a plain shell:

```bash
sudo setcap cap_sys_admin,cap_sys_nice+p build/sunshine
```

Run side-by-side with a packaged Sunshine by shifting the port family in a scratch config
(`port = 48989` → web UI on 48990) and pointing `file_state`/`credentials_file`/`log_path`
at scratch paths, then `./build/sunshine /path/to/that.conf`.

Package: `cpack -G DEB --config ./build/CPackConfig.cmake` → `build/cpack_artifacts/Sunshine.deb`.
