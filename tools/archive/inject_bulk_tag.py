import os

filepath = 'routers/pages.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('</body>', '<script src="/uploads/bulk_import.js"></script>\n</body>', 1)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Bulk import JS tag injected.")
