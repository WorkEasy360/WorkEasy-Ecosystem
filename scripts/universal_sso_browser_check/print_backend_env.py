"""Print, as JSON, the environment the scratch Workeasy360 backend runs with
(only what differs from this process's own), for browser_check.js to hand to
the helper scripts it runs (future_plan.py, the controlled clock).

    USSO_WORK=<dir> python scripts/universal_sso_browser_check/print_backend_env.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare import backend_env

if __name__ == "__main__":
    print(
        json.dumps(
            {
                key: value
                for key, value in backend_env().items()
                if os.environ.get(key) != value
            }
        )
    )
