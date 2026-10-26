"""One native voice send; uncertain receipts are never retried automatically."""
import time


def deliver_voice(queue,room,ident,name,driver,watermark,proofs,*,timeout=10,active=lambda:True):
    part=queue.get(room,ident)
    if not part or part['state']!='ready':raise ValueError('Voice is not ready')
    began=False
    try:
        driver.prepare_voice(name,part['wav'],part['duration'])
        if not active():raise ValueError('Delivery cancelled')
        after=watermark();queue.begin(room,ident,after);began=True
        if not active():raise ValueError('Delivery cancelled')
        driver.commit_voice()
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            matches=[]
            for proof in proofs(after):
                server=str(proof.get('server_id',''));duration=proof.get('voice_duration_ms',0)
                if (proof.get('chat_id')==room and proof.get('is_self') is True and proof.get('type')==34 and
                    type(proof.get('local_id')) is int and proof['local_id']>after and
                    server.isascii() and server.isdecimal() and int(server)>0 and type(duration) is int and
                    part['duration']*1000-300<=duration<=part['duration']*1000+4000):matches.append(proof)
            if len(matches)>1:raise ValueError('Voice receipt is ambiguous')
            if matches:queue.confirm(room,ident,matches[0]);return True
            time.sleep(.5)
        raise TimeoutError('Voice receipt not confirmed')
    except Exception:
        if began:queue.uncertain(room,ident)
        raise
    finally:driver.cleanup_voice()
