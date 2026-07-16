"""Named fault types the transport layer can surface."""


class PeerRejectedSocketFault(Exception):
    """The remote peer actively rejected a new socket."""


class PeerUnreachableFault(Exception):
    """No route to the remote peer exists at all."""
