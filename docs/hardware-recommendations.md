# Hardware Recommendations

Recommended hardware for running CommercialDetector on a single-board computer with HDMI capture.

## Workload Profile

The FFmpeg command runs three lightweight filters (`silencedetect`, `blackdetect`, `scdet`) and outputs to `/dev/null` — no encoding needed. On a modern Mac, the test video processed at **~9.66x realtime**. Only **1x** is needed for live TV. Even hardware running at ~10% of a Mac's speed would keep up.

For live capture, downscaling before running video filters cuts pixel processing while keeping detection accurate:

```
-vf "scale=640:360,blackdetect=...,scdet=..."
```

## Recommended Setup

### HDMI Capture Card: MS2130-based USB 3.0 dongle (~$15-25)

The MS2130 chip is the successor to the ubiquitous MS2109.

- **USB 3.0** — delivers 1080p60 YUV422 (raw) or MJPEG. The older MS2109 is USB 2.0 only and limited to 1080p MJPEG
- **V4L2/UVC class compliant** — works with the `uvcvideo` kernel driver out of the box on Linux, including ARM boards
- **Audio over USB** — captures embedded HDMI audio alongside video (needed for `silencedetect`)
- **Passthrough not needed** — capturing for analysis, not display

Search for "MS2130 HDMI capture card" on Amazon/AliExpress. They come as small USB dongles. Avoid the $5 MS2109-based ones — they're USB 2.0 only, and on RPi USB 2.0 ports they only offer MJPEG, which means more CPU decode overhead.

### SBC: Raspberry Pi 5, 4GB (~$60)

- **CPU**: 4x Cortex-A76 @ 2.4GHz — roughly 3-4x the performance of Pi 4's A72 cores. More than enough for real-time FFmpeg filter processing at 480p
- **USB 3.0 port**: Required for the MS2130 capture card to get raw YUV (avoids MJPEG decode overhead entirely)
- **4GB RAM is plenty**: FFmpeg + Python state machine + MQTT client uses well under 1GB. There's no speed difference between 4GB and 8GB — the 4GB model is actually marginally faster
- **Best Linux support** of any ARM SBC — Raspberry Pi OS, Armbian, Ubuntu all work well
- **Active cooling recommended**: A small heatsink or the official active cooler ($5) prevents throttling during sustained FFmpeg processing

### Why NOT Other Boards

| Board | Issue |
|-------|-------|
| **Raspberry Pi 4** | Would work at 480p but the A72 cores are ~60% slower. Tighter margin. ~$55 so barely saves money |
| **Orange Pi 5** | Superior hardware decode (RK3588S), but Linux FFmpeg HW accel is poorly supported and we don't need decode power anyway |
| **Pi Zero 2W** | Too slow — single A53 cores can't handle real-time video filter processing |
| **Raspberry Pi 5 8GB** | Costs $20 more for RAM you won't use |

## Total Cost

| Component | Price |
|-----------|-------|
| Raspberry Pi 5 4GB | ~$60 |
| Official active cooler | ~$5 |
| MS2130 USB 3.0 capture dongle | ~$15-25 |
| microSD card (32GB+) | ~$8 |
| USB-C power supply (27W) | ~$12 |
| **Total** | **~$100-110** |

## Live Capture Optimization

For the Pi, add a scale filter to the FFmpeg pipeline to downscale before video filters. The current command processes at full input resolution, which is fine at 9.66x speed on a Mac but would waste CPU on the Pi:

```
-vf "scale=640:360,blackdetect=d=0.05:pix_th=0.1,scdet=threshold=10.0"
```

Black frames and scene changes are resolution-independent signals — 640x360 is more than enough to detect them.

## References

- [MS2130 vs MS2109 comparison](https://github.com/awawa-dev/HyperHDR/discussions/499)
- [MS2130 on TinyPilot project](https://github.com/tiny-pilot/tinypilot/discussions/1593)
- [MS2109 Linux V4L2 setup](https://gist.github.com/andersondanilo/3c66fff45e27cbedfb0cb5cc5cc6d0eb)
- [FFmpeg on Raspberry Pi](https://hoop.dev/blog/ffmpeg-on-raspberry-pi-high-performance-media-streaming-and-processing/)
- [Pi 5 4GB vs 8GB benchmarks](https://www.tomshardware.com/raspberry-pi/raspberry-pi-5-4gb-versus-8gb)
- [Linux HW accel challenges on ARM SBCs](https://mayaposch.wordpress.com/2024/04/08/the-tragicomedy-of-linux-raspberry-pi-and-hardware-accelerated-video-on-non-x86-platforms/)
- [TinyPilot HDMI Capture Devices wiki](https://github.com/tiny-pilot/tinypilot/wiki/HDMI-Capture-Devices)
