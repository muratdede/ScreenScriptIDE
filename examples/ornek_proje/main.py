"""
Örnek proje: bölge (Region) ile sınırlı arama.

1. Ekran view'ında "Yakala" ile cihaz ekranını alın.
2. Aranacak butonu sürükleyerek seçin -> "Görsel kaydet" (ör. button.png).
3. Butonun görünebileceği alanı seçin -> "Region ekle" ile koda Region(...) ekleyin.
4. F5 ile debug edin, F9 ile breakpoint koyun.
"""

IMAGE = ARGS.get("image", "button.png")
THRESHOLD = float(ARGS.get("threshold", 0.85))

# Tüm ekran yerine sadece sağ alt çeyrekte ara (daha hızlı ve yanlış eşleşme riski daha düşük)
alan = Region(SCREEN.w // 2, SCREEN.h // 2, SCREEN.w // 2, SCREEN.h // 2)
log(f"Arama alanı: {alan}")

hedef = Pattern(IMAGE).similar(THRESHOLD)
m = alan.exists(hedef, timeout=5)

if m:
    log(f"Bulundu: {m}")
    highlight(m)
    click(m)

    # Eşleşmenin hemen altındaki 100 piksellik şeritte başka bir şey ara:
    # alt = m.below(100)
    # if alt.exists("ok.png", timeout=2):
    #     click(alt.find("ok.png"))
else:
    log(f"Bulunamadı. Bu alandaki en iyi skor: {alan.best_score(hedef):.3f}")
