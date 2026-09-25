"""Uygulama genelinde olay yolu. View'lar birbirini tanımaz; sadece buraya abone olur."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    # proje
    project_opened = Signal(object)          # Project
    project_config_changed = Signal(object)  # Project
    app_config_changed = Signal(object)      # AppConfig (uygulama ayarları: cihaz bağlantısı ...)

    # editör
    open_file_requested = Signal(str, int)   # path, line (0 = yok)
    insert_text_requested = Signal(str)      # aktif editörde imlece metin ekle
    active_file_changed = Signal(str)
    file_renamed = Signal(str, str)          # eski, yeni
    run_requested = Signal(str, bool)        # script, debug
    status_message = Signal(str)             # durum çubuğunda kısa süreli mesaj

    # çalıştırma
    run_started = Signal(str, bool)          # script, debug
    run_finished = Signal(int)               # exit code
    output = Signal(str, str)                # metin, tür: out | err | sys

    # debug
    debug_paused = Signal(dict)              # PAUSED mesajı
    debug_resumed = Signal()
    debug_frame_selected = Signal(int, dict)  # index, frame
    eval_result = Signal(dict)

    # ekran
    screenshot_ready = Signal(object)        # numpy BGR
    screenshot_failed = Signal(str)
    images_changed = Signal()
    pattern_test_requested = Signal(str)     # görsel yolu: ekran görüntüsünde eşleşmeleri göster
