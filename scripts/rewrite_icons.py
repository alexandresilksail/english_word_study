#!/usr/bin/env python3
# Upgrade feature `icon('NAME')` calls to `pic('NAME')` across templates.
# Only single-arg icon() calls whose name is in the feature allowlist are rewritten;
# generic UI glyphs (chevron/arrow/alert/x/trash/help/...) are left as SVG line icons.
import re, os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "app", "templates")

# Feature/function icon names -> thematic images (must match _icons.html pic() map keys)
ALLOW = {
    "vocabulary","word","book","book-open","listening","headphones","speaking","mic",
    "reading","grammar","puzzle","writing","pen-line","games","gamepad","gamepad-2",
    "ai","ai_tutor","bot","stats","chart","progress","achieve","quiz","practice",
    "podcast","favorites","bookmark","wrong","list-checks","admin","dashboard",
    "logout","profile","user","continue","play-circle","path","route","target","goal",
    "level","star","check","check-circle","circle-check","clock","auth","lock",
    "search","hero",
}

# single-arg icon('name') or icon("name")
PAT = re.compile(r"icon\((['\"])([a-zA-Z0-9_-]+)\1\)")

def repl(m):
    q, name = m.group(1), m.group(2)
    if name in ALLOW:
        return f"pic({q}{name}{q})"
    return m.group(0)

# add `, pic` to a `from "X" import icon %}` import (only when icon is the last import)
IMP = re.compile(r'from "(_icons\.html|_macros\.html)" import icon(?=\s*%})')

changed_files = []
total_repl = 0
for path in glob.glob(os.path.join(TEMPLATES, "**", "*.html"), recursive=True):
    name = os.path.basename(path)
    if name in ("_icons.html", "_macros.html"):
        continue  # infrastructure: pic defined here; do not rewrite
    with open(path, encoding="utf-8") as f:
        src = f.read()
    new, n = PAT.subn(repl, src)
    if n:
        # ensure pic is imported in this template
        if "pic(" in new and "import icon, pic" not in new and "import icon, pic" not in new.replace(" ", ""):
            new2 = IMP.sub(r'from "\1" import icon, pic', new)
            if new2 != new:
                new = new2
            else:
                # no matching `import icon %}` found; inject an import after extends or at top
                if "{% extends" in new:
                    new = re.sub(r'({% extends [^%]+%}\n)', r'\1{% from "_icons.html" import icon, pic %}\n', new, count=1)
                else:
                    new = '{% from "_icons.html" import icon, pic %}\n' + new
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        changed_files.append((name, n))
        total_repl += n

print(f"Rewrote {total_repl} icon() calls in {len(changed_files)} files:")
for name, n in changed_files:
    print(f"  {name}: {n}")
