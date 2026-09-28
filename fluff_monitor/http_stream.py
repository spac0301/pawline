"""Observe an HTTP response while forwarding its original wire bytes unchanged.

http.client owns HTTP/1 framing (including chunking). Only decoded Responses
metadata is handed to the reducer; request/answer text is never persisted here.
"""
from __future__ import annotations
import codecs
import gzip
import http.client
import io
import json
import re
import zlib

MAX_BODY = 64 * 1024 * 1024


class RelayReader(io.RawIOBase):
    def __init__(self, source, destination):
        self.source, self.destination = source, destination
        super().__init__()
    def readable(self):
        return True
    def readinto(self, buffer):
        data = self.source.recv(len(buffer))
        if data:
            self.destination.sendall(data)
            buffer[:len(data)] = data
        return len(data)


class RelaySocket:
    def __init__(self, source, destination):
        self.source, self.destination = source, destination
    def makefile(self, mode):
        return io.BufferedReader(RelayReader(self.source, self.destination), buffer_size=65536)


class Sink:
    def __init__(self, consume):self.consume=consume
    def write(self, data):self.consume(data);return len(data)
    def flush(self):pass


class Events:
    """Incremental SSE lines; scan each incoming chunk once without suffix copies."""
    def __init__(self, emit):
        self.emit = emit
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.parts, self.data = [], []
        self.line_size = self.event_size = 0
        self.trailing_cr = False

    def _fragment(self, value):
        if value:
            self.parts.append(value)
            self.line_size += len(value)
        if self.line_size + self.event_size > MAX_BODY:
            raise ValueError("SSE event exceeds observation bound")

    def _line(self):
        line = "".join(self.parts)
        self.parts.clear()
        self.line_size = 0
        if not line:
            if self.data:
                value = "\n".join(self.data)
                if value.strip() != "[DONE]":
                    self.emit(json.loads(value))
            self.data.clear()
            self.event_size = 0
        elif line.startswith("data:"):
            part = line[5:].removeprefix(" ")
            self.data.append(part)
            self.event_size += len(part)

    def feed(self, chunk, final=False):
        text = self.decoder.decode(chunk, final=final)
        if self.trailing_cr and text:
            if text.startswith("\n"):
                text = text[1:]
            self.trailing_cr = False
        start = 0
        for match in re.finditer(r"\r\n|\r|\n", text):
            self._fragment(text[start:match.start()])
            self._line()
            start = match.end()
            self.trailing_cr = match.group() == "\r" and start == len(text)
        self._fragment(text[start:])


def decode_request(data, encoding):
    if len(data)>MAX_BODY:
        raise ValueError('request exceeds observation bound')
    if encoding in ('gzip','x-gzip'):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:data=stream.read(MAX_BODY+1)
    elif encoding=='zstd':
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as stream:data=stream.read(MAX_BODY+1)
    elif encoding=='deflate':
        decoder=zlib.decompressobj();data=decoder.decompress(data,MAX_BODY+1)
    elif encoding not in ('','identity'):
        raise ValueError('unsupported request content encoding')
    if len(data)>MAX_BODY:raise ValueError('decoded request exceeds observation bound')
    obj=json.loads(data)
    if not isinstance(obj,dict):raise ValueError('request is not an object')
    return dict(obj,type='response.create')


def forward_response(client, upstream, method, emit=None, lost=None):
    """Forward exact headers/chunks, separately observing decoded data when supported."""
    response=http.client.HTTPResponse(RelaySocket(upstream,client),method=method)
    try:
        response.begin()
        if 100 <= response.status < 200:
            # HTTPResponse already consumes 100 Continue. Other interim
            # responses are passed through without attempting pooled framing.
            raise http.client.HTTPException('unobserved interim response')
    except http.client.HTTPException:
        response.close()
        raise
    can_reuse=not response.will_close
    content_type=response.getheader('Content-Type','').split(';',1)[0].strip().lower()
    encoding=response.getheader('Content-Encoding','').strip().lower()
    watching=emit is not None and content_type in ('text/event-stream','application/json')
    def fail():
        nonlocal watching
        if watching and lost:
            try:
                lost()
            except Exception:
                pass
        watching=False
    if emit is not None and not watching:
        # HTTP failure itself is known even when the provider sends HTML/text.
        # Publish only a bounded status code, never the response body.
        try:
            if response.status >= 400:
                emit({"type": "error", "error": {"code": f"http_{response.status}",
                      "message": "HTTP error response"}})
            elif lost:
                lost()
        except Exception:
            if lost:
                try:
                    lost()
                except Exception:
                    pass
    events=Events(emit) if watching and content_type=='text/event-stream' else None
    data=bytearray()
    def consume(chunk):
        if events:events.feed(chunk)
        else:
            data.extend(chunk)
            if len(data)>MAX_BODY:raise ValueError('response exceeds observation bound')
    decoder=None
    try:
        if watching:
            if encoding in ('gzip','x-gzip'):decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            elif encoding=='deflate':decoder=zlib.decompressobj()
            elif encoding=='zstd':
                import zstandard
                decoder=zstandard.ZstdDecompressor(max_window_size=MAX_BODY).stream_writer(Sink(consume),write_size=65536,closefd=False)
            elif encoding not in ('','identity'):fail()
    except Exception:fail()
    try:
        while True:
            chunk=response.read1(65536)
            if not chunk:break
            if not watching:continue
            try:
                if decoder is not None:
                    if encoding=='zstd':decoder.write(chunk);continue
                    chunk=decoder.decompress(chunk,MAX_BODY+1)
                    if len(chunk)>MAX_BODY:raise ValueError('decoded response exceeds observation bound')
                consume(chunk)
            except Exception:fail()
        if watching:
            try:
                if events:events.feed(b'',final=True)
                else:
                    obj=json.loads(data)
                    if isinstance(obj,dict):
                        if isinstance(obj.get('error'),dict):emit(dict(type='error',error=obj['error']))
                        elif obj.get('id'):emit(dict(type='response.completed',response=obj))
                        else:fail()
                    else:fail()
            except Exception:fail()
    finally:
        if encoding=='zstd' and decoder is not None:
            try:decoder.close()
            except Exception:fail()
        response.close()
    return can_reuse
