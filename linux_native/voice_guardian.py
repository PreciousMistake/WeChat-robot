"""Cancel an abandoned native recording before the client's duration limit."""
import json
import select
import subprocess
import sys


def main():
    options=json.loads(sys.stdin.readline())
    ready=select.select([sys.stdin],[],[],55)[0]
    if ready and sys.stdin.readline().strip()=='done':return
    try:
        pid=subprocess.run(['xdotool','getwindowpid',options['window']],capture_output=True,text=True,timeout=3,check=True).stdout.strip()
        if pid==str(options['pid']):subprocess.run(['xdotool','key','--window',options['window'],'Escape'],timeout=3,check=False,capture_output=True)
    finally:
        subprocess.run(['pactl','set-default-source',options['previous']],timeout=3,capture_output=True)
        for module in reversed(options['modules']):subprocess.run(['pactl','unload-module',module],timeout=3,capture_output=True)


if __name__=='__main__':main()
