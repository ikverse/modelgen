"""Signs dist\\ModelGen-Setup.exe for the version in modelgen\\__init__.py: writes ModelGen-Setup.exe.sig next to
it, with the private key from installer\\make_signing_key.py. Publish-Release.bat runs it after the build.

It stops (exit code 1) when the key is missing, when it does not match the public key in modelgen\\update_key.py
(installed copies would refuse the release), or when the new signature does not check out.
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_signing_key import KEY, load_private_key, public_key_text  # noqa: E402
from modelgen import __version__, updater  # noqa: E402
from modelgen.update_key import PUBLIC_KEY  # noqa: E402

SETUP = ROOT / "dist" / updater.ASSET


def main():
    if not SETUP.is_file():
        print(f"There is no {SETUP} to sign. Build it first.")
        return 1
    if not KEY.is_file():
        print(f"No signing key at {KEY}. Run installer\\make_signing_key.py once (or put your backup there).")
        return 1
    key = load_private_key()
    if public_key_text(key) != PUBLIC_KEY:
        print("The signing key does not match modelgen\\update_key.py, so installed copies would refuse it.")
        return 1
    sig = SETUP.with_name(updater.SIG)
    sig.write_text(updater.sign(SETUP, __version__, key), encoding="ascii")
    digest = hashlib.sha256(SETUP.read_bytes()).hexdigest()
    if not updater.verify(PUBLIC_KEY, __version__, digest, sig.read_text(encoding="ascii")):
        sig.unlink()
        print("The new signature did not check out, so it was deleted.")
        return 1
    print(f"Signed ModelGen {__version__}: {sig}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
