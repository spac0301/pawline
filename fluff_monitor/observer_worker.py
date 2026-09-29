"""Replaceable reducer process. Never owns relay sockets or the native CLI."""
import json
import sys

from . import __version__
from .identity import set_process_name
from .messages import WsMessage
from .observation import DesktopCapture
from .observer_ipc import PROTOCOL, read_packet, write_packet


def main():
    set_process_name('pawline-observe')
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    cap = None
    try:
        header, payload = read_packet(incoming)
        if header.get('op') != 'start' or header.get('protocol') != PROTOCOL:
            raise ValueError('incompatible observer protocol')
        cap = DesktopCapture(header['directory'], checkpoint=json.loads(payload) if payload else None)
        write_packet(outgoing, dict(ok=True, version=__version__))
        while True:
            header, payload = read_packet(incoming)
            op = header.get('op')
            result = b''
            if op == 'message':
                accepted = cap.feed(WsMessage(header['direction'], payload,
                    header['ts'], header['conn'], header['transport']))
            elif op == 'event':
                accepted = cap.event(header['kind'], header['info'])
            elif op == 'invalidate':
                with cap.condition:
                    cap.last_connection = max(cap.last_connection, header['through'])
                    cap._invalidate_locked(header['reason'])
                accepted = True
            elif op == 'flush':
                accepted = cap.flush()
            elif op == 'handoff':
                result = json.dumps(cap.handoff(), ensure_ascii=False).encode()
                accepted = True
            else:
                raise ValueError('unsupported observer operation')
            write_packet(outgoing, dict(ok=True, accepted=accepted,
                invalid_through=cap.invalid_through), result)
    except EOFError:
        return 0
    finally:
        if cap:
            cap.close(for_handoff=cap.handoff_pause)
