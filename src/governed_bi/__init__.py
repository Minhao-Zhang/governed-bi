"""governed-bi: an agentic BI / Generative-BI system.

Natural-language questions -> grounded, governed, auditable answers over
enterprise relational data. Two harnesses over one shared substrate:

- ``curator`` (build): produces the corpus (the semantic layer moat).
- ``server`` (serve): consumes the corpus to answer, fail-closed.

The full design lives under ``docs/``; start at ``docs/README.md``.
"""

from .config import load_dotenv

__version__ = "0.1.0"

# Local-run convenience: read a git-ignored ``.env`` at the repo root, filling in
# only variables not already set (a real environment variable always wins). This
# is why ``OPENAI_API_KEY`` can live in ``.env`` instead of the shell. See
# ``governed_bi.config.load_dotenv``.
load_dotenv()
