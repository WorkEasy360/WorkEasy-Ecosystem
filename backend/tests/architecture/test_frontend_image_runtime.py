"""The frontend image's runtime stage holds everything ``next.config.ts`` imports.

``next start`` loads ``next.config.ts`` itself, so every file it imports must
exist in the runtime image - not only in the build stage. Release 2026.10.19
crash-looped on a missing ``menu.ts``, and the 2026-10-23 release would have
done the same on ``experiences.ts``. A configuration import is now a visible
change to the Dockerfile, and those imports may not import anything further.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend"


def _relative_imports(source: str) -> list[str]:
    return re.findall(r"""(?m)^import\b[^'"]*['"](\.[^'"]+)['"]""", source)


def test_every_config_import_is_copied_into_the_runtime_stage():
    config = (FRONTEND / "next.config.ts").read_text(encoding="utf-8")
    dockerfile = (ROOT / "deploy" / "docker" / "frontend.Dockerfile").read_text(
        encoding="utf-8"
    )
    runtime = dockerfile.rsplit("\nFROM ", 1)[1]
    imports = _relative_imports(config)
    assert imports, "the scan finds next.config.ts's imports"
    for spec in imports:
        path = (Path("/app") / spec.removeprefix("./")).as_posix()
        target = path if path.endswith((".ts", ".mjs", ".js")) else f"{path}.ts"
        assert "/app/next.config.ts" in runtime
        assert re.search(rf"COPY --from=build\b[^\n]*\s{re.escape(target)}\s", runtime), (
            f"{target} is imported by next.config.ts but not copied into the runtime image"
        )
        source = (FRONTEND / target.removeprefix("/app/")).read_text(encoding="utf-8")
        assert not re.findall(r"(?m)^import\s(?!type\b)", source), (
            f"{target} imports other modules at runtime; copy them too or keep it self-contained"
        )
