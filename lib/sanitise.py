"""sanitise.py -- remove the operator's identity from anything published.

ONE implementation, imported by `package-public.py` and `build-corpus-export.py`.
This codebase has shipped a single convention as two expressions three times
(`min(3, n)`), and a substitution that must be byte-exact across two publishing
paths is exactly that shape: the day one of them gains a fourth encoding and the
other does not, a username ships.

Everything works on BYTES. The identifier is pure ASCII, so a byte-level
substitution is exact and it preserves what a text round-trip would quietly
rewrite: the UTF-8 BOM several files in this tree carry, CRLF line endings, and
any non-UTF-8 sequence inside a Speos log.

Measured on the corpus 2026-08-05: 535 occurrences across 216 files, in three
encodings, and the username in paths was the ONLY identifier present -- no name,
no email, no share link, no host name.
"""
import hashlib
import re

USER = b"<user>"

# The JSON-escaped form MUST be tried first: the single-backslash pattern would
# otherwise match its first half and leave a mangled path behind.
SUBS = [
    (re.compile(rb"(?i)(C:\\\\Users\\\\)bob\b"), b"\\g<1>" + USER),  # JSON-escaped
    (re.compile(rb"(?i)(C:\\Users\\)bob\b"), b"\\g<1>" + USER),      # backslash
    (re.compile(rb"(?i)(C:/Users/)bob\b"), b"\\g<1>" + USER),        # forward
]

IDENT = re.compile(rb"(?i)C:(?:\\\\|\\|/)Users(?:\\\\|\\|/)bob\b")


def sanitise(data):
    """Bytes in, bytes out."""
    for rx, rep in SUBS:
        data = rx.sub(rep, data)
    return data


def leaks(data):
    """True if the identifier survives in any encoding."""
    return bool(IDENT.search(data))


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def text(data):
    """Decode for line/JSON inspection ONLY -- never for writing back."""
    return data.decode("utf-8-sig", errors="replace")


def sha(data, n=12):
    h = hashlib.sha256(data).hexdigest()
    return h[:n] if n else h
