"""
Example project: search limited to a region (Region).

1. Capture the device screen with "Capture" in the Screen view.
2. Drag to select the button to search -> "Save image" (e.g. button.png).
3. Select the area where the button can appear -> insert Region(...) into code with "Region".
4. Debug with F5, set breakpoints with F9.
"""

IMAGE = ARGS.get("image", "button.png")
THRESHOLD = float(ARGS.get("threshold", 0.85))

# search only in the bottom-right quarter instead of the whole screen (faster, fewer false matches)
area = Region(SCREEN.w // 2, SCREEN.h // 2, SCREEN.w // 2, SCREEN.h // 2)
log(f"Search area: {area}")

target = Pattern(IMAGE).similar(THRESHOLD)
m = area.exists(target, timeout=5)

if m:
    log(f"Found: {m}")
    highlight(m)
    click(m)

    # search something else in the 100 pixel strip right below the match:
    # below = m.below(100)
    # if below.exists("ok.png", timeout=2):
    #     click(below.find("ok.png"))
else:
    log(f"Not found. Best score in this area: {area.best_score(target):.3f}")
