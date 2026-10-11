#!/usr/bin/env python3
# Verify every pic('NAME') and url_for('static', filename='img/...') reference resolves to a real file.
import re, os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "app", "templates")
STATIC = os.path.join(ROOT, "app", "static")

PIC_MAP = {
    "vocabulary":"ill-word.svg","word":"ill-word.svg","book":"ill-book.svg","book-open":"ill-book.svg",
    "listening":"ill-listening.svg","headphones":"ill-listening.svg",
    "speaking":"ill-speaking.svg","mic":"ill-speaking.svg",
    "reading":"ill-reading.svg",
    "grammar":"ill-grammar.svg","puzzle":"ill-grammar.svg",
    "writing":"ill-writing.svg","pen-line":"ill-writing.svg",
    "games":"ill-games.svg","gamepad":"ill-games.svg","gamepad-2":"ill-games.svg",
    "ai":"ill-tutor.svg","ai_tutor":"ill-tutor.svg","bot":"ill-tutor.svg",
    "stats":"ill-achieve.svg","chart":"ill-achieve.svg","progress":"ill-achieve.svg","achieve":"ill-achieve.svg",
    "quiz":"ill-quiz.svg","practice":"ill-quiz.svg",
    "podcast":"ill-podcast.svg",
    "favorites":"themes/favorites.png","bookmark":"themes/favorites.png",
    "wrong":"themes/wrong.png","list-checks":"themes/wrong.png",
    "admin":"themes/admin.png","dashboard":"themes/admin.png",
    "logout":"themes/logout.png",
    "profile":"themes/profile.png","user":"themes/profile.png",
    "continue":"ill-continue.svg","play-circle":"ill-continue.svg",
    "path":"ill-goal.svg","route":"ill-goal.svg","target":"ill-goal.svg","goal":"ill-goal.svg",
    "level":"ill-level.svg","star":"ill-star.svg",
    "check":"ill-check.svg","check-circle":"ill-check.svg","circle-check":"ill-check.svg",
    "clock":"ill-clock.svg","auth":"ill-lock.svg","lock":"ill-lock.svg","search":"ill-search.svg","hero":"ill-hero.svg",
}

problems = []
pic_refs = {}
for path in glob.glob(os.path.join(TEMPLATES, "**", "*.html"), recursive=True):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # pic('name')
    for m in re.finditer(r"pic\((['\"])([a-zA-Z0-9_-]+)\1", src):
        name = m.group(2)
        pic_refs.setdefault(name, 0)
        pic_refs[name] += 1
        img = PIC_MAP.get(name)
        if not img:
            problems.append(f"[pic-unmapped] {os.path.relpath(path)}: pic('{name}') has no image map (falls back to SVG - ok)")
            continue
        fp = os.path.join(STATIC, "img", img)
        if not os.path.exists(fp):
            problems.append(f"[MISSING PIC IMAGE] {os.path.relpath(path)}: pic('{name}') -> img/{img} NOT FOUND")
    # url_for('static', filename='img/...')
    for m in re.finditer(r"filename='(img/[^']+)'", src):
        rel = m.group(1)
        fp = os.path.join(STATIC, rel)
        if not os.path.exists(fp):
            problems.append(f"[MISSING STATIC IMG] {os.path.relpath(path)}: {rel} NOT FOUND")

print("Distinct pic() names used:", len(pic_refs))
for k in sorted(pic_refs):
    print(f"  {k} -> {PIC_MAP.get(k, 'SVG-fallback')} ({pic_refs[k]}x)")
print()
if problems:
    print("PROBLEMS FOUND:")
    for p in problems:
        print("  " + p)
else:
    print("OK: all pic()/static image references resolve to existing files.")
