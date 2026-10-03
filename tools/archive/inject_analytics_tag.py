import os

filepath = 'routers/pages.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('</body>', '<script src="/uploads/analytics_widget.js"></script>\n</body>')

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Analytics widget JS tag injected.")
