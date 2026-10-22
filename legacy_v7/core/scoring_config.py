"""Public configuration adapter. Never load the application's config or secrets.

Authorized policy packs must be explicitly provisioned by the integrator.
No norms, private knowledge bases, scores or customer records ship in this repo.
"""
import os
from pathlib import Path

BASE_DIR = str(Path(os.environ.get("ENGINEERING_RULES_DIR", Path(__file__).parent / "empty_policy_pack")).resolve())
