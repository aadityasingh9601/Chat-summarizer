"""Entry point kept at the repository root.

The implementation lives in ``whatsapp_parser.cli`` so the package can also be
installed and used as the ``chat-summarizer`` command or via
``python -m whatsapp_parser``.
"""

from whatsapp_parser.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
