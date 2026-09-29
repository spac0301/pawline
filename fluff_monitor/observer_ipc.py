"""Length-bounded JSON headers and binary payloads on inherited private pipes."""
import json
import struct

PROTOCOL = 1
MAX_HEADER = 128 * 1024
MAX_PAYLOAD = 32 * 1024 * 1024


def exact(stream, count):
    data = bytearray()
    while len(data) < count:
        part = stream.read(count - len(data))
        if not part:
            raise EOFError('observer pipe closed')
        data.extend(part)
    return bytes(data)


def read_packet(stream):
    size, = struct.unpack('!I', exact(stream, 4))
    if size > MAX_HEADER:
        raise ValueError('observer header exceeds limit')
    header = json.loads(exact(stream, size))
    length = header.get('payload_size') if isinstance(header, dict) else None
    if type(length) is not int or not 0 <= length <= MAX_PAYLOAD:
        raise ValueError('observer payload exceeds limit')
    return header, exact(stream, length)


def write_packet(stream, header, payload=b''):
    head = json.dumps(dict(header, payload_size=len(payload)), ensure_ascii=False).encode()
    if len(head) > MAX_HEADER or len(payload) > MAX_PAYLOAD:
        raise ValueError('observer packet exceeds limit')
    for data in (struct.pack('!I', len(head)), head, payload):
        view = memoryview(data)
        while view:
            written = stream.write(view)
            if not written:
                raise EOFError('observer pipe closed')
            view = view[written:]
    stream.flush()
