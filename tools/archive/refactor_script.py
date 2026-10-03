import ast
import os
import re

os.makedirs('routers', exist_ok=True)

with open('maintestfinal2.py', 'r', encoding='utf-8') as f:
    source = f.read()

lines = source.splitlines()
tree = ast.parse(source)

endpoints = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        is_endpoint = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and getattr(dec.func.value, 'id', '') == 'app':
                is_endpoint = True
        
        # also check for auth_middleware (middleware is not router, keep it in main)
        if is_endpoint and 'middleware' not in getattr(node, 'name', ''):
            # get decorators too
            endpoints.append(node)

# Group endpoints
groups = {
    'pages': [],
    'auth': [],
    'webhooks': [],
    'api': []
}

for ep in endpoints:
    name = ep.name
    if name.endswith('_page') or name in ['gateway_monitor', 'gateway_placement_editor']: # pages
        groups['pages'].append(ep)
    elif 'auth' in name or name in ['login_page', 'admin_login_page', 'client_login_page', 'logout', 'me']:
        groups['auth'].append(ep)
    elif 'webhook' in name:
        groups['webhooks'].append(ep)
    else:
        if name.endswith('_editor'):
            groups['pages'].append(ep)
        else:
            groups['api'].append(ep)

# Extract code blocks
def get_code(node):
    # node.lineno is 1-indexed, node.end_lineno is 1-indexed
    # decorator lineno can be earlier
    start = node.lineno - 1
    if node.decorator_list:
        start = node.decorator_list[0].lineno - 1
    end = node.end_lineno
    return "\n".join(lines[start:end])

extracted_code = {k: [] for k in groups}

# To delete safely from bottom to top
to_delete = []

for g_name, eps in groups.items():
    for ep in eps:
        start = ep.lineno - 1
        if ep.decorator_list:
            start = ep.decorator_list[0].lineno - 1
        end = ep.end_lineno
        
        code = "\n".join(lines[start:end])
        # Replace @app. with @router.
        code = code.replace('@app.get', '@router.get')
        code = code.replace('@app.post', '@router.post')
        code = code.replace('@app.put', '@router.put')
        code = code.replace('@app.delete', '@router.delete')
        
        extracted_code[g_name].append(code)
        to_delete.append((start, end))

# Write routers
for g_name, code_blocks in extracted_code.items():
    if not code_blocks: continue
    with open(f'routers/{g_name}.py', 'w', encoding='utf-8') as f:
        f.write("from fastapi import APIRouter, BackgroundTasks\n")
        f.write("from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse\n")
        f.write("from maintestfinal2 import *\n\n")
        f.write("router = APIRouter()\n\n")
        f.write("\n\n".join(code_blocks))

# Delete from maintestfinal2.py
to_delete.sort(key=lambda x: x[0], reverse=True)
for start, end in to_delete:
    del lines[start:end]

# Append router includes
footer = [
    "\n# --- ROUTERS ---\n",
    "from routers.pages import router as pages_router",
    "from routers.auth import router as auth_router",
    "from routers.api import router as api_router",
    "from routers.webhooks import router as webhooks_router",
    "app.include_router(auth_router)",
    "app.include_router(pages_router)",
    "app.include_router(api_router)",
    "app.include_router(webhooks_router)",
]

lines.extend(footer)

with open('maintestfinal2_refactored.py', 'w', encoding='utf-8') as f:
    f.write("\n".join(lines))

print("Refactoring complete. Check maintestfinal2_refactored.py")
