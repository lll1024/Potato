import sys,pathlib,hashlib,json
out=pathlib.Path('/tmp/potato86-independent-use')
for arg in sys.argv[1:]:
 p=pathlib.Path(arg);b=p.read_bytes();lines=b.decode().splitlines()
 print('\n### '+str(p)+'\n'+'\n'.join(f'{i+1}: {v}' for i,v in enumerate(lines)))
 with (out/'access.jsonl').open('a') as f:f.write(json.dumps({'path':str(p),'lines':[1,len(lines)],'sha256':hashlib.sha256(b).hexdigest()})+'\n')
