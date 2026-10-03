import os

filepath = 'routers/pages.py'

if os.path.exists(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    pwa_meta = '''</title>
    <link rel="manifest" href="/uploads/manifest.json">
    <meta name="theme-color" content="#08111f">
    <meta name="apple-mobile-web-app-capable" content="yes">'''
    
    content = content.replace('</title>', pwa_meta)
    
    sw_script = '''<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/uploads/service-worker.js');
  });
}
</script>
</body>'''
    
    content = content.replace('</body>', sw_script)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("PWA tags injected.")
