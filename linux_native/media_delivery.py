"""One prepared native media commit, correlated by original content MD5."""
import hashlib
from pathlib import Path
import time


def deliver_media(jobs,job,name,driver,watermark,proofs,*,active=lambda:True,timeout=12):
    ident=job['id'];path=Path(job['payload']['path'])
    if not path.resolve().is_relative_to((jobs.state/'.runtime/plugin_spool').resolve()) or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=job['payload']['sha256']:raise ValueError('Media spool identity changed')
    began=False
    try:
        driver.prepare_media(name,path,job['kind'])
        if not active():raise ValueError('Delivery cancelled')
        after=watermark();jobs.begin(ident,after);began=True
        if not active():raise ValueError('Delivery cancelled')
        driver.commit_media()
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            current=jobs.get(ident);matches=[p for p in proofs(after) if jobs.proof_matches(current,p)]
            if len(matches)>1:raise ValueError('Media acknowledgement is ambiguous')
            if matches:jobs.confirm(ident,matches[0]);return True
            time.sleep(.3)
        raise TimeoutError('Media send not confirmed')
    except Exception:
        jobs.fail(ident)
        raise
    finally:driver.cancel_media()
