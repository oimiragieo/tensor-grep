"""Looks up an address for a hostname."""

import socket


def resolve_hostname(hostname):
    return socket.gethostbyname(hostname)
