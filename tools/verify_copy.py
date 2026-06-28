import os
dst = r"C:\Users\serg9\Downloads\Parser v1.28.3.6"
out = os.path.join(dst, "tools", "verify_result.txt")
files = []
for root, _, fnames in os.walk(dst):
    for f in fnames:
        files.append(os.path.relpath(os.path.join(root, f), dst))
with open(out, "w", encoding="utf-8") as fh:
    fh.write(f"count={len(files)}\n")
    for p in sorted(files)[:50]:
        fh.write(p + "\n")
