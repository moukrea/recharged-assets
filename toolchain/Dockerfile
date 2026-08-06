# Recharged assets toolchain — pinned encoder versions (spec §9.3).
# Build:  docker build -t ghcr.io/moukrea/recharged-assets-toolchain:1 toolchain/
# Every CI job that encodes/validates textures runs inside this image so the
# outputs stay deterministic across runners and time.

FROM ubuntu:24.04

ARG KTX_VERSION=4.4.2
ARG ASTCENC_VERSION=5.7.0
# Pinned by commit because neither project tags frequently enough.
# These are the exact commits validated by the local bake-off run.
ARG BC7ENC_COMMIT=b9438627eef73a1157e84201b6fa6eb2ffd6d9f0
ARG ETCPAK_COMMIT=9110365ba6916c9554c231c5cdcac77b2618cb89
# NOTE: Compressonator was evaluated and dropped — its 4.5.52 Linux CLI
# cannot save any decodable image format (png/bmp both fail with exit 0),
# so no independent round-trip quality metric can be computed from it, and
# bc7enc_rdo + etcpak + astcenc already cover every target format.

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip bzip2 xz-utils \
        python3 python3-pip python3-venv \
        build-essential cmake ninja-build git libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# --- KTX-Software (ktx create/validate, libktx) -----------------------------
RUN curl -sL "https://github.com/KhronosGroup/KTX-Software/releases/download/v${KTX_VERSION}/KTX-Software-${KTX_VERSION}-Linux-x86_64.tar.bz2" \
        | tar xj -C /opt \
    && ln -s /opt/KTX-Software-${KTX_VERSION}-Linux-x86_64/bin/* /usr/local/bin/ \
    && cp -r /opt/KTX-Software-${KTX_VERSION}-Linux-x86_64/lib/* /usr/local/lib/ \
    && ldconfig \
    && ktx --version

# --- Arm astcenc -------------------------------------------------------------
RUN curl -sL "https://github.com/ARM-software/astc-encoder/releases/download/${ASTCENC_VERSION}/astcenc-${ASTCENC_VERSION}-linux-x64.zip" -o /tmp/astcenc.zip \
    && unzip -q /tmp/astcenc.zip -d /tmp/astcenc \
    && install /tmp/astcenc/bin/astcenc-avx2 /usr/local/bin/astcenc \
    && rm -rf /tmp/astcenc* \
    && astcenc -help >/dev/null

# --- bc7enc_rdo (BC1-5/BC7) ---------------------------------------------------
RUN git clone https://github.com/richgel999/bc7enc_rdo /tmp/bc7enc \
    && cd /tmp/bc7enc && git checkout ${BC7ENC_COMMIT} \
    && sed -i '1i #include <cstdint>' ert.h \
    && cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 . \
    && ninja -C build \
    && install build/bc7enc /usr/local/bin/ \
    && rm -rf /tmp/bc7enc

# --- etcpak (ETC2 / EAC R11 / RG11) ------------------------------------------
RUN git clone https://github.com/wolfpld/etcpak /tmp/etcpak \
    && cd /tmp/etcpak && git checkout ${ETCPAK_COMMIT} \
    && cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 . \
    && ninja -C build \
    && install build/etcpak /usr/local/bin/ \
    && rm -rf /tmp/etcpak

# --- Python pipeline dependencies --------------------------------------------
RUN pip3 install --break-system-packages --no-cache-dir \
        pillow numpy jsonschema pytest zstandard

WORKDIR /work
