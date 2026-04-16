import os
import re

src_dir = r"d:\hackathon april\hackstrom-track3\frontend\src"

def fix_vars(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Find var(--...) that are NOT preceded by a quote and NOT followed by a quote.
    # Note: Javascript variables can't be `var(--something)`. So any `var(--something)` in TSX is exactly our broken string!
    # Except in template literals: `${var(--text-muted)}`? No, if we put `var(...)` inside `${}`, it's an error because `var` is a JS keyword.
    # Wait, did we replace `"#06060b"` inside template strings?
    # e.g., `1px solid ${"#1e293b"}` became `1px solid ${var(--border)}`. So `var(--border)` is there.
    # If we replace `var(--border)` with `"var(--border)"`, it becomes `1px solid ${"var(--border)"}`. That correctly evaluates to `1px solid var(--border)` in the string!
    
    # We can just do a regex replace for `var(--[a-zA-Z0-9-]+)` that are not inside quotes, 
    # but actually it's easier to just blindly replace `var(--something)` with `"var(--something)"` 
    # IF it's not already quoted.
    
    fixed = re.sub(r'(?<![\'"])var\(--([a-zA-Z0-9-]+)\)(?![\'"])', r'"var(--\1)"', content)
    
    if fixed != content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(fixed)

for root_dir, _, files in os.walk(src_dir):
    for f in files:
        if f.endswith('.tsx') or f.endswith('.ts'):
            path = os.path.join(root_dir, f)
            fix_vars(path)

print("Fixed var syntax.")
