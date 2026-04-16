import os
import glob
import re

src_dir = r"d:\hackathon april\hackstrom-track3\frontend\src"

def replace_in_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    for old, new in replacements:
        content = re.sub(old, new, content, flags=re.IGNORECASE)
        
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

replacements = [
    # Backgrounds
    (r'(?i)"#06060b"', 'var(--bg-primary)'),
    (r'(?i)"#0d0d1a"', 'var(--bg-card)'),
    (r'(?i)"#111122"', 'var(--bg-primary)'), 
    (r'(?i)"#0a0a12"', 'var(--header-bg)'),  
    # Wait, text color for header should be header-text (#f8fafc)
    # The header has title color #94a3b8 and #e2e8f0. I will map them below carefully.
    
    # Borders
    (r'(?i)"#1e293b"', 'var(--border)'),
    (r'(?i)"#0f172a"', 'var(--border)'), 
    (r'(?i)"#1e3a5f"', 'var(--accent-blue)'),
    
    # Text
    (r'(?i)"#e2e8f0"', 'var(--text-primary)'), 
    (r'(?i)"#94a3b8"', 'var(--text-muted)'),
    (r'(?i)"#475569"', 'var(--text-secondary)'),
    (r'(?i)"#334155"', 'var(--text-muted)'),
    
    # Brand colors
    (r'(?i)"#3B82F6"', 'var(--accent-blue)'),
    (r'(?i)"#22c55e"', 'var(--accent-green)'),
    (r'(?i)"#f59e0b"', 'var(--accent-amber)'),
    (r'(?i)"#ef4444"', 'var(--accent-red)'),
    
    # RGBA opacity variations
    (r'(?i)"rgba\(59,\s*130,\s*246,([^)]+)\)"', r'"rgba(37, 99, 235, \1)"'),   # Blue
    (r'(?i)"rgba\(34,\s*197,\s*94,([^)]+)\)"', r'"rgba(22, 163, 74, \1)"'),    # Green
    (r'(?i)"rgba\(245,\s*158,\s*11,([^)]+)\)"', r'"rgba(217, 119, 6, \1)"'),   # Amber
    (r'(?i)"rgba\(239,\s*68,\s*68,([^)]+)\)"', r'"rgba(220, 38, 38, \1)"'),    # Red
    
    (r'(?i)"rgba\(37, 99, 235, 0.035\)"', '"#f1f5f9"'), # table hover

    # Labels use system-ui 
    # For example: fontFamily: "'JetBrains Mono', monospace" -> "'system-ui, sans-serif'" for labels
    # but the instructions say "keep JetBrains Mono for data values".
]

# Update all TSX files
for root_dir, _, files in os.walk(src_dir):
    for f in files:
        if f.endswith('.tsx') or f.endswith('.ts'):
            path = os.path.join(root_dir, f)
            replace_in_file(path, replacements)
            
# Post-process specific font changes:
# e.g., in DeclarationPage, change label fonts if necessary, although JetBrains is okay for now if we just replace colors.

print("Done TSX replacements.")
