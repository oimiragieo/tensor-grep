"""Core module docstring sentence. A second sentence that first-sentence extraction must drop."""


class Widget:
    """A widget class with a documented method. More detail that must not appear."""

    def render(self, size):
        """Render the widget at the given size."""
        return size


def bare_documented():
    """A bare module-level function with a docstring."""
    return True


def bare_undocumented(x, y):
    return x + y
