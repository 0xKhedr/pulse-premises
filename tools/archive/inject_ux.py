import os

files_to_modify = ['routers/pages.py', 'routers/auth.py']

ux_tags = '''
<!-- UX Systems -->
<link href="https://cdnjs.cloudflare.com/ajax/libs/intro.js/7.2.0/introjs.min.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/intro.js/7.2.0/intro.min.js"></script>
<script src="/uploads/onboarding_tour.js"></script>
<script src="/uploads/search_system.js"></script>
<script src="/uploads/realtime_toasts.js"></script>
</body>
'''

for filepath in files_to_modify:
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace all instances of </body> with the UX injection
        new_content = content.replace('</body>', ux_tags)
        
        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Injected UX scripts into {filepath}")
        else:
            print(f"No </body> tags found in {filepath}")
