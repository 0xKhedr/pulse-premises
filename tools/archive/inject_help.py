import os

files_to_modify = ['routers/pages.py', 'routers/auth.py']
script_tag = '<script src="/uploads/help_system.js"></script>\n</body>'

for filepath in files_to_modify:
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace all instances of </body> with the script injection
        new_content = content.replace('</body>', script_tag)
        
        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Injected help script into {filepath}")
        else:
            print(f"No </body> tags found in {filepath}")
