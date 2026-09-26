"""ScreenScript IDE — SikuliX tarzı editör. Uygulamanın kendisi budur.

Çalıştırma (depo kökünden):
    pip install -r requirements.txt
    python main.py [proje_klasörü]

Arka uç işler (ADB yakalama, görsel script API'si) editor.plugins altında eklenti olarak bulunur.
Not: Bu dosya, editor.core'un Qt'siz modülleri, editor.agent ve editor.plugins Qt import etmez;
script süreci hafif kalsın diye.
"""

__version__ = "0.1.0"
