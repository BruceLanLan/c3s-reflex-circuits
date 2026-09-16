# Run before every build (extra_scripts in platformio.ini): a source file or a build flag
# that carries a Wi-Fi network, a password or a device token stops the build here, rather
# than reaching an image. The check itself is scripts/check_credentials.py.

import sys
from pathlib import Path

Import("env")  # noqa: F821  (SCons runs this file without __file__, so the path comes from the build)

ROOT = Path(env["PROJECT_DIR"]).resolve().parents[1]  # noqa: F821
sys.path.insert(0, str(ROOT / "scripts"))

from check_credentials import check  # noqa: E402

findings = check(ROOT)
for line in findings:
    print(f"credential in the build: {line}", file=sys.stderr)
if findings:
    print("\nCredentials are typed on the device (Network page) and kept in its NVS. Nothing that\n"
          "becomes an image may carry them: see docs/FIRMWARE.md. Refusing to build.", file=sys.stderr)
    Exit(1)  # noqa: F821  (SCons provides it)
print("credentials: none in the sources or the build file")
