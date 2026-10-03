import os

files_to_modify = ['routers/pages.py']

graphs_tags = '''
<!-- Chart.js & Graphs System -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="/uploads/graphs.js"></script>
</body>
'''

for filepath in files_to_modify:
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # We previously replaced </body> with UX tags. 
        # So we can just replace </body> again to append this before it.
        new_content = content.replace('</body>', graphs_tags)
        
        if new_content != content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Injected graphs scripts into {filepath}")
