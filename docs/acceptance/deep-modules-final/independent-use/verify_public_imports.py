"""静态核对验收脚本的每项仓库导入都在对应公开入口 __all__。"""
import ast, hashlib, json
from pathlib import Path
OUT=Path("/tmp/potato86-independent-use")
ROOT=Path("/Users/jarviliu/projects/travel")
def read(p):
    data=p.read_bytes()
    with (OUT/"access.jsonl").open("a") as stream:
        stream.write(json.dumps({"path":str(p),"lines":[1,len(data.decode().splitlines())],"sha256":hashlib.sha256(data).hexdigest(),"purpose":"脚本公开导入静态核对"})+"\n")
    return data
def check(script):
    data=read(script)
    project=[]; external=[]
    for node in ast.walk(ast.parse(data)):
        if isinstance(node,ast.ImportFrom):
            if not node.module.startswith("travel_app."):
                external.append({"module":node.module,"names":[name.name for name in node.names],"classification":"标准库或第三方 SDK，非本仓库能力入口"})
                continue
            path=ROOT.joinpath(*node.module.split("."),"__init__.py")
            init=read(path)
            declarations=[]
            for statement in ast.parse(init).body:
                if isinstance(statement,ast.Assign) and any(isinstance(target,ast.Name) and target.id=="__all__" for target in statement.targets):
                    declarations=ast.literal_eval(statement.value)
            for name in node.names:
                project.append({"module":node.module,"name":name.name,"public_entry":str(path),"in_all":name.name in declarations,"entry_sha256":hashlib.sha256(init).hexdigest()})
        elif isinstance(node,ast.Import):
            external.extend({"module":name.name,"classification":"标准库或第三方 SDK，非本仓库能力入口"} for name in node.names)
    return {"script":str(script),"script_sha256":hashlib.sha256(data).hexdigest(),"project_imports":project,"external_imports":external,"all_project_imports_explicitly_exported":all(item["in_all"] for item in project)}
before=check(OUT/"public_use_after_contract-before-import-fix.py")
after=check(OUT/"public_use_after_contract.py")
result={"before":before,"after":after,"scope":"仓库能力导入逐项以公开入口 __all__ 核对；标准库和第三方 SDK 列入清单但不套用本仓库导出约束"}
(OUT/"public-import-audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
assert not before["all_project_imports_explicitly_exported"]
assert after["all_project_imports_explicitly_exported"]
print(json.dumps({"before":before["project_imports"],"after":after["project_imports"],"passed":True},ensure_ascii=False,indent=2))
