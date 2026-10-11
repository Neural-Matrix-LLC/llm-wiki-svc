"""Summarise a junit XML: failures grouped by message, and per-file pass/fail/skip counts.
   python tests/qa_sso/tools/junit_summary.py run/logs/junit/X.xml [--groups]"""
import collections
import sys
import xml.etree.ElementTree as ET

path = sys.argv[1]
root = ET.parse(path).getroot()
per = collections.defaultdict(lambda: collections.Counter())
groups = collections.defaultdict(list)
for c in root.iter("testcase"):
    mod = c.get("classname", "").rsplit(".", 1)[-1]
    f = c.find("failure")
    if f is None:
        f = c.find("error")
    if f is not None:
        per[mod]["fail"] += 1
        groups[(f.get("message") or "")[:260].replace("\n", " ")].append(c.get("name"))
    elif c.find("skipped") is not None:
        per[mod]["skip"] += 1
    else:
        per[mod]["pass"] += 1
tot = collections.Counter()
for mod in sorted(per):
    tot.update(per[mod])
    print(f"{mod:42s} pass={per[mod]['pass']:4d} fail={per[mod]['fail']:3d} skip={per[mod]['skip']:3d}")
print(f"{'TOTAL':42s} pass={tot['pass']:4d} fail={tot['fail']:3d} skip={tot['skip']:3d}")
if "--groups" in sys.argv:
    for m, ns in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"\n[{len(ns)}] {ns[:4]}\n    {m}")
