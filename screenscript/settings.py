"""Çalışma zamanı ayarları (SikuliX'teki Settings)."""

from dataclasses import dataclass


@dataclass
class Settings:
    min_similarity: float = 0.7      # Pattern'de similar() verilmezse kullanılan eşik
    auto_wait_timeout: float = 3.0   # find/wait/click için varsayılan bekleme (sn)
    scan_rate: float = 3.0           # bekleme sırasında saniyedeki tarama sayısı
    click_delay: float = 0.0         # her tıklamadan sonra bekleme (sn)
    debug_dir: str = "debug"         # highlight() görüntülerinin kaydedildiği klasör
    save_on_fail: bool = False       # FindFailed olduğunda ekranı debug_dir'e kaydet
