import ast
import json

def analyze_fastapi(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
            
        endpoints = []
        classes = []
        
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = []
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Attribute):
                            decorators.append(f"{dec.func.value.id}.{dec.func.attr}")
                        elif isinstance(dec.func, ast.Name):
                            decorators.append(dec.func.id)
                endpoints.append({"name": node.name, "decorators": decorators})
                
        print(f"Total Endpoints/Functions: {len(endpoints)}")
        for ep in endpoints:
            if any('app.' in d or 'router.' in d for d in ep['decorators']):
                print(f"Route: {ep['name']} -> {ep['decorators']}")
                
    except Exception as e:
        print(e)

analyze_fastapi('maintestfinal2.py')
