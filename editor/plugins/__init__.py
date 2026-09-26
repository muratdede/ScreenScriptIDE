"""Editörün arka uç eklentileri. Editör bunları paket olarak içerir (include); ayrı giriş noktaları yoktur.

    adb_capture    ADB erişimi + kare yakalama (screencap / h264 akışı)
    screenscript   SikuliX tarzı görsel otomasyon API'si (adb_capture'ı kullanır)

Eklentiler Qt import etmez ve editor.core / editor.ui'ya bağımlı değildir; script süreci
(editor.agent.runner) hafif kalsın diye. Eklentiler arası importlar göreli yapılır (from ..adb_capture).
"""
