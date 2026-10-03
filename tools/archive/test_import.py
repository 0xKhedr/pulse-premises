import sys
try:
    import maintestfinal2
    app = maintestfinal2.app
    print("App imported successfully")
except Exception as e:
    import traceback
    traceback.print_exc()
