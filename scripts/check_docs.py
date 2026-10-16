"""Check local Markdown links in maintained project documentation."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def heading_anchors(source: str) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", source, re.MULTILINE):
        base = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        occurrence = occurrences.get(base, 0)
        occurrences[base] = occurrence + 1
        anchors.add(f"{base}-{occurrence}" if occurrence else base)
    return anchors


def check_documentation(root: Path = ROOT) -> list[str]:
    documents = [root / "README.md", *(root / "docs").rglob("*.md")]
    documents.extend((root / "reference").glob("*.md"))
    errors: list[str] = []
    for document in documents:
        source = document.read_text(encoding="utf-8")
        source = re.sub(r"```.*?```", "", source, flags=re.DOTALL)
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", source):
            parsed = urlsplit(link.strip("<>"))
            if parsed.scheme or parsed.netloc:
                continue
            target = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            if not target.exists():
                errors.append(f"Broken link: {document.relative_to(root)} -> {link}")
            elif parsed.fragment and target.suffix == ".md":
                anchors = heading_anchors(target.read_text(encoding="utf-8"))
                if unquote(parsed.fragment) not in anchors:
                    errors.append(f"Missing heading: {document.relative_to(root)} -> {link}")
    return errors


if __name__ == "__main__":
    failures = check_documentation()
    if failures:
        raise SystemExit("\n".join(failures))
    print("Documentation checks passed: local links and heading anchors.")
