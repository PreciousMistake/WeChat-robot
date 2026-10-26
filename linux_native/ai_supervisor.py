"""Host service wrapper; cloud credentials stay inside the non-root worker."""
import signal
import subprocess
import time


def main():
    stop=[False]
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.__setitem__(0,True))
    process=subprocess.Popen(['docker','exec','-i','--user','12000','--workdir','/home/wechat/native-access',
        'wechat-native-lab','python3','-u','-m','linux_native.drafts'],stdin=subprocess.PIPE,text=True)
    try:
        while process.poll() is None and not stop[0]:time.sleep(.5)
    finally:
        if process.poll() is None:
            try:process.stdin.write('stop\n');process.stdin.flush()
            except BrokenPipeError:pass
        process.stdin.close()
        process.wait(timeout=40)
    if process.returncode and not stop[0]:raise SystemExit(1)


if __name__=='__main__':main()
