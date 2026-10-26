"""Persist native mention text before committing and verify actual target IDs."""
import time


def deliver_mentions(jobs,job,name,members,driver,watermark,proofs,*,active=lambda:True,timeout=12):
    ident=job['id']
    commit_attempted=False
    try:
        driver.prepare_at(name,job['payload']['text'],members)
        if not active():raise ValueError('Delivery cancelled')
        jobs.set_prepared_text(ident,driver.prepared_text)
        after=watermark();jobs.begin(ident,after)
        if not active():raise ValueError('Delivery cancelled')
        commit_attempted=True
        driver.commit()
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            current=jobs.get(ident);matches=[p for p in proofs(after) if jobs.proof_matches(current,p)]
            if len(matches)>1:raise ValueError('Native mention receipt is ambiguous')
            if matches:jobs.confirm(ident,matches[0]);return True
            time.sleep(.3)
        raise TimeoutError('Native mention send not confirmed')
    except Exception:
        jobs.fail(ident);raise
    finally:
        if not commit_attempted:driver.cleanup_at()
