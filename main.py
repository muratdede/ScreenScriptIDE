"""
Örnek kullanım.

  pip install opencv-python numpy

  python main.py --port 5555 --interval 0.5 --preview
  python main.py --port 5555 --interval 1 --save-dir shots --save-every 5
  python main.py --adb "C:\\Program Files\\BlueStacks_nxt\\HD-Adb.exe" --port 5555 --preview
"""

import argparse
import logging
import time

from adb_capture import (AdbDevice, create_source, DiskSaver, LatestFrameBuffer,
                         PeriodicCapturer, PreviewWindow, Region, ScreencapFormat)


def parse_args():
    p = argparse.ArgumentParser(description="ADB periyodik ekran yakalama")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5555, help="BlueStacks Ayarlar > Gelişmiş'te yazan port")
    p.add_argument("--adb", help="adb yolu (boşsa otomatik bulunur)")
    p.add_argument("--interval", type=float, default=1.0, help="Yakalama aralığı (sn)")
    p.add_argument("--format", choices=[f.value for f in ScreencapFormat], default="stream")
    p.add_argument("--crop", help="x,y,w,h")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--save-dir", help="Verilirse kareler buraya kaydedilir")
    p.add_argument("--save-every", type=float, default=0.0, help="Kaç sn'de bir kaydedilsin (0 = her kare)")
    p.add_argument("--duration", type=float, help="Kaç sn çalışsın (boşsa Ctrl+C / q'ya kadar)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    device = AdbDevice.from_host_port(args.host, args.port, adb_path=args.adb)
    source = create_source(device, ScreencapFormat(args.format))
    buffer = LatestFrameBuffer()

    capturer = PeriodicCapturer(
        source,
        interval=args.interval,
        consumers=[buffer],
        crop=Region.parse(args.crop) if args.crop else None,
    )
    if args.save_dir:
        capturer.subscribe(DiskSaver(args.save_dir, every_seconds=args.save_every))

    deadline = time.monotonic() + args.duration if args.duration else None

    def keep_going() -> bool:
        return capturer.is_running and (deadline is None or time.monotonic() < deadline)

    with capturer:
        try:
            if args.preview:
                PreviewWindow(buffer, stats_provider=lambda: capturer.stats).run(keep_going)
            else:
                # Burası bot/analiz kodunun yeri: her yeni kareyi bekle ve işle
                last = -1
                while keep_going():
                    frame = buffer.wait_next(last, timeout=1.0)
                    if frame is None:
                        continue
                    last = frame.index
                    logging.info("Kare #%d %dx%d (%.0f ms)", frame.index, frame.width,
                                 frame.height, frame.capture_ms)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
