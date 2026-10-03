import ast
import os
import re

with open('maintestfinal2.py', 'r', encoding='utf-8') as f:
    source = f.read()

tree = ast.parse(source)

# We will collect all functions that have @app. decorators
endpoints = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        is_endpoint = False
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and getattr(dec.func.value, 'id', '') == 'app':
                is_endpoint = True
        if is_endpoint:
            endpoints.append(node)

print(f"Found {len(endpoints)} endpoints")

# Let's see the names of the first 10
for ep in endpoints[:10]:
    print(ep.name)
