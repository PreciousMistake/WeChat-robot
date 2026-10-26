FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl python3 python3-gi python3-pip python3-venv \
    xvfb xauth openbox x11-utils x11vnc novnc websockify dbus-x11 at-spi2-core \
    gir1.2-atspi-2.0 xdotool xclip pulseaudio pulseaudio-utils ffmpeg tini \
    python3-pil tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    fonts-noto-cjk libatomic1 libnss3 libgbm1 libasound2t64 libgtk-3-0t64 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 \
    libxkbcommon-x11-0 libxcb-xinerama0 libxcb-cursor0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
ARG WECHAT_SHA256=a0db8caeee8d1e6cfe0aa5ce58710270f8d74a90ff4920f53a3fc22aa0956fe9
RUN curl -fL --retry 3 --connect-timeout 20 --max-time 600 \
      https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb -o /tmp/wechat.deb \
    && echo "$WECHAT_SHA256  /tmp/wechat.deb" | sha256sum -c - \
    && dpkg-deb -f /tmp/wechat.deb Package Version Architecture > /wechat-package.txt \
    && apt-get update && apt-get install -y --no-install-recommends /tmp/wechat.deb \
    && rm -f /tmp/wechat.deb && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/wechat-python \
    && /opt/wechat-python/bin/pip install --no-cache-dir silk-python==0.2.8 websocket-client==1.9.2 cryptography==49.0.0
ENV PATH="/opt/wechat-python/bin:${PATH}"
RUN useradd --create-home --uid 12000 --shell /bin/bash wechat
COPY desktop.py /opt/lab/desktop.py
USER wechat
ENV HOME=/home/wechat DISPLAY=:99 XAUTHORITY=/home/wechat/.Xauthority \
    QT_ACCESSIBILITY=1 QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 \
    QT_AUTO_SCREEN_SCALE_FACTOR=0 QT_SCALE_FACTOR=1 QT_FONT_DPI=96 \
    LIBGL_ALWAYS_SOFTWARE=1
WORKDIR /home/wechat
ENTRYPOINT ["/usr/bin/tini","--","dbus-run-session","--","python3","-u","/opt/lab/desktop.py"]
