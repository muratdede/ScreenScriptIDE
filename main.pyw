"""ScreenScript IDE — uygulamanın tek giriş noktası.

    pip install -r requirements.txt
    python main.py                  # son projeyi açar
    python main.py proje_klasoru    # belirli bir klasörü proje olarak açar
"""

import sys

from editor.app import main

if __name__ == "__main__":
    sys.exit(main())
