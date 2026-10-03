import os

files_to_modify = ['routers/pages.py', 'routers/auth.py']
css_tag = '<link rel="stylesheet" href="/uploads/bright_theme.css">\n</head>'

for filepath in files_to_modify:
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace all instances of </head> with the CSS injection
        new_content = content.replace('</head>', css_tag)
        
        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Injected CSS into {filepath}")
        else:
            print(f"No </head> tags found in {filepath}")
