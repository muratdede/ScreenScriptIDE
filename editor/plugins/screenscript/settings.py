"""Runtime settings (like Settings in SikuliX)."""

from dataclasses import dataclass


@dataclass
class Settings:
    min_similarity: float = 0.7      # threshold used when similar() is not given on a Pattern
    auto_wait_timeout: float = 3.0   # default wait for find/wait/click (s)
    scan_rate: float = 3.0           # scans per second while waiting
    click_delay: float = 0.0         # wait after every click (s)
    debug_dir: str = "debug"         # folder where highlight() images are saved
    save_on_fail: bool = False       # save the screen to debug_dir on FindFailed
    ocr_backend: str = "auto"        # text recognition engine: auto, rapid, windows
    ocr_gpu: bool = True             # RapidOCR uses the GPU (DirectML / CUDA) when the onnxruntime build has it
