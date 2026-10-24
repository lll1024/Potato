import subprocess,sys,json,pathlib,time
out=pathlib.Path('/tmp/potato86-independent-use')
cmd=sys.argv[1]
index=len(list(out.glob('command-*.log')))+1
log=out/f'command-{index:03}.log'
p=subprocess.run(cmd,shell=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
log.write_text(p.stdout)
with (out/'commands.jsonl').open('a') as f:f.write(json.dumps({'command':cmd,'returncode':p.returncode,'output_file':str(log)},ensure_ascii=False)+'\n')
print(p.stdout,end='')
sys.exit(p.returncode)
