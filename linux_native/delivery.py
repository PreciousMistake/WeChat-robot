"""One verified delivery attempt; UI failure after commit is ambiguous."""
import time


def deliver(queue,room,ident,name,driver,watermark,proofs,*,timeout=10,active=lambda:True):
    row=queue.get(room,ident)
    if not row or row['state']!='draft':raise ValueError('Not a fresh draft')
    driver.prepare(name,row['reply'])
    after=watermark()
    if not active():raise ValueError('Delivery cancelled')
    queue.begin_send(room,ident,after)
    try:
        if not active():raise ValueError('Delivery cancelled')
        driver.commit()
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            for proof in proofs(after):
                server=str(proof.get('server_id',''))
                if (proof.get('is_self') is True and proof.get('text')==row['reply'] and proof.get('chat_id')==room and
                    type(proof.get('local_id')) is int and proof['local_id']>after and
                    server.isascii() and server.isdecimal() and int(server)>0):
                    queue.confirm(room,ident,proof)
                    return True
            time.sleep(.5)
        raise TimeoutError('Outgoing message not confirmed')
    except Exception:
        queue.uncertain(room,ident)
        raise
