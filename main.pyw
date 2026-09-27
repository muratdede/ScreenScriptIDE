"""ScreenScript IDE — the single entry point of the application.

    pip install -r requirements.txt
    python main.pyw                  # opens the last project
    python main.pyw project_folder   # opens a specific folder as the project
"""

import sys

from editor.app import main

if __name__ == "__main__":
    sys.exit(main())
