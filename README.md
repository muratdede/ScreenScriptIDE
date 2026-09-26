# ScreenScript IDE

SikuliX tarzı editör. Uygulamanın kendisi editördür; arka uç işler (ADB yakalama, görsel script API'si)
`editor/plugins/` altında eklenti olarak bulunur. Tek giriş noktası kökteki `main.py`'dir.

## Kurulum ve çalıştırma

```
pip install -r requirements.txt
python main.py                           # son projeyi açar
python main.py examples/ornek_proje      # belirli bir klasörü proje olarak açar
```

Proje = herhangi bir klasör. İlk açılışta `images/`, `.screenscript.json` ve `main.py` oluşturulur.
Cihaz bağlantısı (host, port, adb, yakalama formatı): **Ayarlar (Ctrl+Alt+S)** — tüm projelerde ortak.
Eşik, ölçekler, ana script gibi ayarlar: **Proje Ayarları (Ctrl+Alt+Shift+S)**.

## Kısayollar

| Kısayol | İşlev |
|---|---|
| Ctrl+Space | Kod tamamlama |
| Ctrl+Shift+Space | Parametre bilgisi (`(` ve `,` yazınca otomatik açılır) |
| Ctrl+/ | Yorum aç/kapat |
| Ctrl+G | Satıra git |
| Ctrl+R / Ctrl+F5 | Çalıştır (debug'sız) |
| F5 | Debug başlat / devam et |
| F6 | Duraklat |
| Shift+F5 | Durdur |
| F9 | Breakpoint (ya da satır numarasının soluna tıklayın) |
| F10 / F11 / Shift+F11 | Üstünden / içine / dışına adımla |
| Ctrl+Shift+G | Cihaz ekranını yakala |
| Alt+1 … Alt+7 | View'ları aç/kapat |

Üzerine gelince: fonksiyon açıklaması; `"button.png"` gibi string'lerde görsel önizlemesi.

## View'lar

- **Proje**: dosya ağacı. Sağ tık menüsünde yeni script, ana script yap, çalıştır/debug, yeniden adlandır, sil.
- **Görseller**: `images/` (ve proje kökündeki) görseller. Çift tık koda ekler; sağ tık → ekran görüntüsünde test.
  Klasöre elle dosya atmanız yeterli: liste anında güncellenir (klasör izleme + 2 sn'de bir kontrol +
  editöre dönünce tazeleme). Ekran view'ındaki test listesi de aynı kaynaktan beslenir.
- **Ekran**: Yakala → sürükleyerek alan seç → *Görsel kaydet* / *Region ekle* / *Konum ekle*.
  *Eşleşmeleri göster* ile seçili görselin skorlarını ekranda görürsünüz (seçim varsa sadece o bölgede).
- **Konsol**: script çıktısı. Debug'da duraklatılmışken alttaki satırda ifade değerlendirilir
  (seçili çağrı yığını çerçevesinde), çalışırken `input()` için stdin'e yazılır.
- **Değişkenler**, **Çağrı Yığını**, **Breakpoint'ler**.

## Mimari

```
main.py            Tek giriş noktası (editor.app.main)
requirements.txt
examples/          Örnek proje(ler)
editor/
  app.py           EditorApplication (QApplication + EditorContext + MainWindow)
  core/            Qt'siz: project.py, protocol.py | Qt servisleri: events (EventBus), breakpoints,
                   completion (CompletionProvider), session (ScriptSession), grabber, context, run_controller
  agent/           Script süreci: runner (süreç girişi), context_factory (ProjectConfig -> ScriptContext),
                   debugger (bdb tabanlı)
  ui/              main_window, editor_tabs, code_editor, highlighter, dialogs, theme, views/
  plugins/         Arka uç eklentileri (Qt'siz, editöre bağımlı değil)
    adb_capture/   ADB erişimi + kare yakalama (screencap / h264 akışı)
    screenscript/  SikuliX tarzı görsel otomasyon API'si
```

Editör eklentileri paket olarak içerir (`from ..plugins.screenscript import ...`); eklentiler arası
importlar göreli yapılır (`from ..adb_capture import ...`). Yeni bir arka uç işi `editor/plugins/` altına
ayrı bir paket olarak eklenir; kendi giriş noktası (main) olmaz.

Script, editörden ayrı bir Python sürecinde çalışır (`editor.agent.runner`, ScriptSession başlatır); böylece takılan bir
script editörü dondurmaz ve Durdur her zaman çalışır. Debug komutları satır bazlı JSON ile TCP
üzerinden gider. Debugger sadece proje dosyalarını izler; kütüphane kodu normal hızda çalışır.

### Genişletme noktaları

| Ne | Nasıl |
|---|---|
| Yeni view | `DockView`'dan türet (`view_id`, `title`, `default_area`, `build()`), `views.DEFAULT_VIEWS`'e ekle ya da `MainWindow(view_classes=...)` |
| Yeni tamamlama kaynağı | `CompletionProvider`'dan türet, `ctx.completion`'a ver (`FallbackCompletionProvider` ile zincirlenebilir) |
| Farklı script kurulumu | `agent.ContextFactory` alt sınıfı (`configure_matcher`, `configure_input`…) |
| Görsel listesi | `ctx.images` (`ImageRepository`) — `changed(list)` sinyaline abone olun |
| Yeni proje ayarı | `ProjectConfig`'e alan ekle (eski json dosyaları çalışmaya devam eder) |
| Tema | `Theme`'den türet, `EditorApplication(theme=...)` |
| View'lar arası iletişim | `EventBus` sinyalleri; view'lar birbirini doğrudan tanımaz |

Eşleştirme: projeler **her zaman çoklu ölçekte** tarar (varsayılan 0.8, 0.9, 1.0, 1.1, 1.2).
Ölçekler Proje Ayarları'ndan değiştirilebilir; Ekran view'ındaki test de aynı eşleştiriciyi kullanır,
bulunan ölçeği de gösterir (ör. "ölçek ≈1.10x").

Not: Duraklat (F6), script bir API çağrısının içindeyken (ör. `exists(..., timeout=10)`) basılırsa
çağrı bittikten sonraki ilk satırda durur.
