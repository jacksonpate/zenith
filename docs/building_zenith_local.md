# Building Zenith locally

> **On NVIDIA, CUDA is not optional.** `kmsgrab.cpp` refuses the KMS capture path outright
> for an NVENC session when `SUNSHINE_BUILD_CUDA` is undefined, and reverts to copying every
> frame GPU → RAM → GPU:
>
> ```
> Warning: Attempting to use NVENC without CUDA support. Reverting back to GPU -> RAM -> GPU
> ```
>
> So a build without CUDA cannot do zero-copy capture on an NVIDIA host at all — however the
> virtual display is placed, and whatever capabilities the binary has. The official releases
> ship CUDA; a local build has to opt in. `zenith-display doctor` detects this and says so.
> It is easy to miss otherwise: it is one warning line at startup, and its only symptom is a
> frame rate that will not climb.

## Linux Mint 22.x / Ubuntu 24.04, NVIDIA

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
  -DCMAKE_INSTALL_PREFIX=/usr -DSUNSHINE_ASSETS_DIR=share/zenith \
  -DSUNSHINE_EXECUTABLE_PATH=/usr/bin/zenith \
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

## Fedora 44, hybrid NVIDIA + AMD laptop

Verified on `pate-fedora` (RTX 4050 Mobile + Radeon 680M, KDE/Wayland). Fedora ships no CUDA
package and RPM Fusion does not carry `nvcc`, so the toolkit comes from NVIDIA's runfile —
installed **toolkit-only into the build tree**, touching no system path and not the driver:

```bash
curl -L -C - -o build/cuda.run \
  https://developer.download.nvidia.com/compute/cuda/13.1.1/local_installers/cuda_13.1.1_590.48.01_linux.run
chmod +x build/cuda.run
mkdir -p build/cudatmp    # the runfile needs a tmpdir with several GB; /tmp is a 7.5G tmpfs
./build/cuda.run --silent --toolkit --toolkitpath="$PWD/build/cuda" \
  --no-opengl-libs --no-man-page --no-drm --override --tmpdir="$PWD/build/cudatmp"
rm -rf build/cudatmp build/cuda.run

# glibc 2.41+ clashes with CUDA 13's math_functions.h; the patch is in-tree
patch -p2 --backup --directory=build/cuda < packaging/linux/patches/x86_64/cuda-13-math_functions.patch

cmake -B build -G Ninja -S . \
  -DBUILD_WERROR=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
  -DSUNSHINE_ASSETS_DIR=share/zenith -DSUNSHINE_EXECUTABLE_PATH=/usr/bin/zenith \
  -DSUNSHINE_ENABLE_DRM=ON -DSUNSHINE_ENABLE_KWIN=ON -DSUNSHINE_ENABLE_PORTAL=ON \
  -DSUNSHINE_ENABLE_WAYLAND=ON -DSUNSHINE_ENABLE_X11=ON \
  -DSUNSHINE_ENABLE_CUDA=ON \
  -DCMAKE_CUDA_COMPILER="$PWD/build/cuda/bin/nvcc" \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/gcc-15
ninja -C build -j4
```

Three things that bite here:

- **`gcc-15`, not the default.** Fedora 44's `gcc` is 16.x, which CUDA 13.1's `host_config.h`
  rejects outright. `dnf install gcc15` if it is not already present.
- **The in-tree patch is required**, not optional. Without it nvcc fails on
  `/usr/include/bits/mathcalls.h: exception specification is incompatible`. `linux_build.sh`
  applies it via `--cuda-patches`; doing the steps by hand means applying it by hand.
- **Cap the job count.** `ninja` defaults to one job per core; on a 16-core / 14 GB laptop
  that overcommits badly enough for the kernel to start killing desktop applications. `-j4`
  peaks around 6 GB.

Or let the script do all of it: `./scripts/linux_build.sh --cuda-runfile --cuda-patches --num-processors 4`.

## After building

A source build has no postinst, so nothing has granted it the file capabilities the deb/rpm
apply. Let the autopilot find that and everything else it needs:

```bash
zenith-display doctor        # says what is wrong, offers to fix it — answer Y
zenith-display fix           # or apply it all without being asked
```

Skipping this is not a hard failure, which is what makes it worth calling out: without
`cap_sys_admin` the KMS capture path cannot open a DRM framebuffer, so capture silently falls
back to the desktop portal and present-paced capture turns off with it (vblank pacing only
exists on the KMS path). The stream still works — it is just slower, permanently. The log says
`Failed to gain CAP_SYS_ADMIN` and nothing else. By hand it is:

```bash
sudo setcap cap_sys_admin,cap_sys_nice+p build/sunshine
```

Run side-by-side with a packaged Sunshine by shifting the port family in a scratch config
(`port = 48989` → web UI on 48990) and pointing `file_state`/`credentials_file`/`log_path`
at scratch paths, then `./build/sunshine /path/to/that.conf`.

Package: `cpack -G DEB --config ./build/CPackConfig.cmake` → `build/cpack_artifacts/Sunshine.deb`.
