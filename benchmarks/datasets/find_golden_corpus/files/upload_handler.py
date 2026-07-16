"""Accepts a client file upload in chunks."""


def stream_multipart_payload(chunks_iter, sink):
    for chunk in chunks_iter:
        sink.write(chunk)
    return sink
