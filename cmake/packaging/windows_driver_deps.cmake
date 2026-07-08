# Fetch the signed Windows virtual-display driver payload at package time.
#
# Zenith bundles SudoVDA (SudoMaker, MIT/CC0): a UMDF indirect-display driver
# whose self-signed package installs on stock Windows once its certificate is
# in the Root + TrustedPublisher stores — no test mode, Secure Boot stays on
# (the Apollo project ships the identical flow at scale). nefcon performs the
# driver install. Binaries are downloaded, never rebuilt here.
#
# Outputs: VDD_DRIVER_AVAILABLE / NEFCON_AVAILABLE, VDD_DRIVER_DIR / NEFCON_DRIVER_DIR

include_guard(GLOBAL)

if(NOT WIN32)
    return()
endif()

option(FETCH_DRIVER_DEPS "Download the VDD driver payload from GitHub releases" ON)

set(VDD_DRIVER_VERSION "sudovda-v1.10.9" CACHE STRING "SudoVDA payload release tag")
set(NEFCON_VERSION "v1.17.40" CACHE STRING "nefcon release tag")

set(_VDD_REPO "jacksonpate/build-deps")
set(_NEFCON_REPO "nefarius/nefcon")

set(DRIVER_DEPS_CACHE "${CMAKE_BINARY_DIR}/_driver_deps" CACHE PATH "Driver dependency cache")
set(VDD_DRIVER_DIR "${DRIVER_DEPS_CACHE}/sudovda")
set(NEFCON_DRIVER_DIR "${DRIVER_DEPS_CACHE}/nefcon")

set(VDD_DRIVER_AVAILABLE FALSE)
set(NEFCON_AVAILABLE FALSE)

if(NOT FETCH_DRIVER_DEPS)
    message(STATUS "VDD driver downloads disabled (FETCH_DRIVER_DEPS=OFF)")
    return()
endif()

if(NOT EXISTS "${VDD_DRIVER_DIR}/SudoVDA.inf")
    set(_vdd_zip "${VDD_DRIVER_DIR}.zip")
    file(MAKE_DIRECTORY "${VDD_DRIVER_DIR}")
    file(DOWNLOAD
        "https://github.com/${_VDD_REPO}/releases/download/${VDD_DRIVER_VERSION}/sudovda-v1.10.9.zip"
        "${_vdd_zip}" STATUS _vdd_status TIMEOUT 300)
    list(GET _vdd_status 0 _vdd_code)
    if(_vdd_code EQUAL 0)
        file(ARCHIVE_EXTRACT INPUT "${_vdd_zip}" DESTINATION "${VDD_DRIVER_DIR}")
    else()
        message(WARNING "SudoVDA download failed; the installer will ship without one-click VDD")
    endif()
endif()
if(EXISTS "${VDD_DRIVER_DIR}/SudoVDA.inf")
    set(VDD_DRIVER_AVAILABLE TRUE)
endif()

# nefcon ships per-arch subdirectories (arm64/, x64/) — pick x64 explicitly
# so alphabetical globbing can't hand us the ARM binary.
if(NOT EXISTS "${NEFCON_DRIVER_DIR}/nefconw.exe")
    set(_nefcon_zip "${NEFCON_DRIVER_DIR}.zip")
    file(MAKE_DIRECTORY "${NEFCON_DRIVER_DIR}")
    file(DOWNLOAD
        "https://github.com/${_NEFCON_REPO}/releases/download/${NEFCON_VERSION}/nefcon_${NEFCON_VERSION}.zip"
        "${_nefcon_zip}" STATUS _nefcon_status TIMEOUT 300)
    list(GET _nefcon_status 0 _nefcon_code)
    if(_nefcon_code EQUAL 0)
        set(_nefcon_tmp "${NEFCON_DRIVER_DIR}-extract")
        file(ARCHIVE_EXTRACT INPUT "${_nefcon_zip}" DESTINATION "${_nefcon_tmp}")
        file(GLOB_RECURSE _nefcon_exe "${_nefcon_tmp}/*x64/nefconw.exe")
        if(_nefcon_exe)
            list(GET _nefcon_exe 0 _nefcon_exe)
            file(COPY_FILE "${_nefcon_exe}" "${NEFCON_DRIVER_DIR}/nefconw.exe")
        endif()
        file(REMOVE_RECURSE "${_nefcon_tmp}")
    else()
        message(WARNING "nefcon download failed; the installer will ship without one-click VDD install")
    endif()
endif()
if(EXISTS "${NEFCON_DRIVER_DIR}/nefconw.exe")
    set(NEFCON_AVAILABLE TRUE)
endif()

message(STATUS "VDD payload: sudovda=${VDD_DRIVER_AVAILABLE} nefcon=${NEFCON_AVAILABLE}")
