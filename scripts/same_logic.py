"""Check that a file's code is unchanged except for docstrings and comments.

Usage: python same_logic.py <path>   (compares the working copy with the last commit)
"""
import ast, subprocess, sys

def strip(tree):
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)

path = sys.argv[1]
old = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True, check=True).stdout
new = open(path, encoding="utf-8").read()
print(path, "→", "SAME LOGIC ✅ (only docstrings/comments changed)" if strip(ast.parse(old)) == strip(ast.parse(new)) else "LOGIC CHANGED ❌")
