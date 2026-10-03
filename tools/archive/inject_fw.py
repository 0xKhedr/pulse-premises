import os

filepath = 'routers/pages.py'

if os.path.exists(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Replace all instances of </body> with the injected script
    new_content = content.replace('</body>', '<script src="/uploads/generate_firmware.js"></script>\n</body>')
    
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Injected generate_firmware.js into {filepath}")
